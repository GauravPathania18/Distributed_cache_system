import threading
import time

import requests

from database.exceptions import DatabaseUnavailableError


class WarmupRateLimiter:
    """
    Layer 6.3 - recovery throttling (Section 12-13).

    Caps how fast warm-up reads reach the database so a restart
    cannot turn into 10,000 immediate queries. Every DB read in a
    warm-up loop does `limiter.wait()` first, which sleeps out the
    gap since the previous read.

    `requests_per_second <= 0` (or None) disables throttling.
    Thread-safe: several nodes may warm concurrently against one
    limiter and share the budget.
    """

    def __init__(self, requests_per_second):

        if requests_per_second is not None and requests_per_second < 0:
            raise ValueError(
                "requests_per_second must be >= 0 (0 = unlimited)"
            )

        self.requests_per_second = requests_per_second or 0

        self.interval = (
            1.0 / requests_per_second
            if requests_per_second
            else 0.0
        )

        self.last_request = None

        self.lock = threading.Lock()

    def wait(self) -> None:
        """Block until the next database read is allowed."""

        with self.lock:

            if self.interval <= 0:
                self.last_request = time.monotonic()
                return

            now = time.monotonic()

            if self.last_request is not None:

                elapsed = now - self.last_request

                if elapsed < self.interval:
                    time.sleep(self.interval - elapsed)

            self.last_request = time.monotonic()


class CacheWarmup:
    """
    Layer 6.3 - cache warm-up for RECOVERING nodes (Sections 2-19).

    When a node restarts its memory is empty. Before it is allowed
    back into the hash ring (the Layer 6.4 / 6.3 recovery gate) we
    reload the cluster's hottest keys it owns:

        hot keys (router view + merged node statistics)
            -> ownership filter (ring view WITH this node)
            -> rate limiter
            -> database (source of truth; missing keys skipped)
            -> direct write to the recovering node
            -> READY

    Design rules baked in:

        - Warm-up is an OPTIMIZATION, the database stays the
          correctness source (Section 18). A key missing from the
          database is skipped, not an error (Section 15).
        - Database failure during warm-up marks the attempt failed
          and BLOCKS the READY gate; the health loop retries each
          pass until the database returns (Section 16). FAILED
          stays reserved for health-check failures.
        - The recovering node is gated out of the live ring, so
          ownership comes from ring_view(node) and writes bypass
          routing via write_to_node (Section 10 corrected).
        - Top-WARMUP_LIMIT keys = recovered (Section 17); the rest
          loads lazily on cache MISS.
        - Layer 6.5 (cold-start seeding): when there is NO hot-key
          history AT ALL - a fresh boot, where the ranking is empty
          because no traffic has flowed yet - an optional seeder
          supplies a popularity-ordered pool of dataset keys
          instead, capped at capacity x fill_ratio per node so the
          seed can never evict itself. Traffic history always wins:
          seeding only runs while the ranking is empty.

    Status states: pending -> running -> done | failed, plus
    "disabled". Only ensure()/_run() mutate status (single writer).
    """

    def __init__(
        self,
        node_manager,
        database,
        hot_key_manager,
        distributed_cache,
        circuit_breaker=None,
        enabled=True,
        limit=1000,
        rate_limit=100.0,
        merge_limit=100,
        stats_fetcher=None,
        seeder=None
    ):

        self.node_manager = node_manager
        self.database = database
        self.hot_keys = hot_key_manager
        self.cache = distributed_cache

        self.circuit_breaker = circuit_breaker

        self.enabled = bool(enabled)
        self.limit = limit
        self.merge_limit = merge_limit
        self.rate_limit = rate_limit

        # callable(node, limit) -> [[key, count], ...] | None
        # None = built-in fan-out to the node /stats/top-keys.
        self.stats_fetcher = stats_fetcher

        # Layer 6.5 - CacheSeeder (or None = seeding off): supplies
        # the candidate pool when there is no hot-key history.
        # Duck-typed on purpose: candidates() -> [key, ...] and
        # budget(node) -> int.
        self.seeder = seeder

        self.limiter = WarmupRateLimiter(rate_limit)

        self._lock = threading.Lock()

        # node -> status dict (single writer: ensure/_run)
        self._status = {}

        # node -> worker thread (joinable for tests/demos)
        self._threads = {}

        self._installed = False

    # -----------------------------------------
    # WIRING (trigger + retry + READY gate)
    # -----------------------------------------

    def install(self) -> bool:
        """
        Wire the warm-up into the node manager (idempotent):

            lifecycle listener -> start warm-up on RECOVERING,
                                  forget it on an explicit restart
            health hook        -> retry failed warm-ups each pass
            recovery gate      -> READY requires warm-up done
        """

        with self._lock:

            if self._installed:
                return False

            self._installed = True

        self.node_manager.add_listener(self._on_lifecycle)
        self.node_manager.add_health_hook(self._retry_recovering)
        self.node_manager.set_recovery_gate(self.recovery_ready)

        return True

    def _on_lifecycle(self, event) -> None:
        """Lifecycle listener (runs post-lock, so HTTP is safe here)."""

        from cluster.node_state import NodeState

        if event.new_state is NodeState.RECOVERING:
            self.ensure(event.node)

        elif event.new_state is NodeState.STARTING:
            # Explicit restart: whatever was warmed before no longer
            # describes this process - start over on the next
            # RECOVERING.
            with self._lock:
                self._status.pop(event.node, None)

    def _retry_recovering(self, manager) -> None:
        """Health-pass hook: retry warm-ups that failed while gated."""

        from cluster.node_state import NodeState

        with manager.lock:

            recovering = [
                node
                for node, state in manager.node_states.items()
                if state is NodeState.RECOVERING
            ]

        for node in recovering:

            with self._lock:
                state = self._status.get(node, {}).get("state")

            if state == "failed":
                self.ensure(node)

    def recovery_ready(self, node: str) -> bool:
        """
        The recovery gate: True when `node` may leave RECOVERING.

        done/disabled or never asked -> satisfied (a manager with
        no warm-up record keeps plain Layer 6.1 behavior).
        pending/running/failed        -> hold the node back.
        """

        if not self.enabled:
            return True

        with self._lock:
            entry = self._status.get(node)

        if entry is None:
            return True

        return entry["state"] in ("done", "disabled")

    # -----------------------------------------
    # TRIGGER / STATUS
    # -----------------------------------------

    def ensure(self, node: str) -> bool:
        """
        Start a warm-up for `node` unless one is already pending,
        running or done. Returns True when a new attempt started.
        """

        if not self.enabled:

            with self._lock:
                self._status[node] = self._entry("disabled")

            return False

        with self._lock:

            entry = self._status.get(node)

            if entry and entry["state"] in (
                "pending", "running", "done"
            ):
                return False

            attempts = (entry or {}).get("attempts", 0) + 1

            self._status[node] = self._entry(
                "pending", attempts=attempts
            )

        try:

            thread = threading.Thread(
                target=self._run,
                args=(node,),
                daemon=True,
                name=f"cache-warmup-{attempts}"
            )

            with self._lock:
                self._threads[node] = thread

            thread.start()

        except Exception as error:

            with self._lock:
                self._status[node] = self._entry(
                    "failed",
                    attempts=attempts,
                    error=repr(error)
                )

            return False

        return True

    def status(self) -> dict:
        """Snapshot of every known warm-up record."""

        with self._lock:
            return {
                node: dict(entry)
                for node, entry in self._status.items()
            }

    def _entry(self, state, attempts=0, **extra) -> dict:

        entry = {
            "state": state,
            "attempts": attempts,
            "source": None,
            "keys_attempted": 0,
            "keys_loaded": 0,
            "keys_not_owned": 0,
            "keys_missing": 0,
            "keys_write_failed": 0,
            "started_at": None,
            "finished_at": None,
            "duration": None,
            "error": None,
        }

        entry.update(extra)

        return entry

    # -----------------------------------------
    # WORKER
    # -----------------------------------------

    def _run(self, node: str) -> None:
        """Thread body: running -> warm_node -> done/failed."""

        with self._lock:

            entry = self._status.get(node) or self._entry("running")

            entry["state"] = "running"
            entry["started_at"] = time.time()

        try:

            result = self.warm_node(node)

        except Exception as error:

            result = {
                "state": "failed",
                "error": repr(error),
                "duration": None
            }

        with self._lock:

            entry = self._status.get(node) or self._entry(
                result.get("state", "failed")
            )

            entry.update({
                "state": result.get("state", "failed"),
                "source": result.get("source"),
                "keys_attempted": result.get("keys_attempted", 0),
                "keys_loaded": result.get("keys_loaded", 0),
                "keys_not_owned": result.get("keys_not_owned", 0),
                "keys_missing": result.get("keys_missing", 0),
                "keys_write_failed": result.get(
                    "keys_write_failed", 0
                ),
                "finished_at": time.time(),
                "duration": result.get("duration"),
                "error": result.get("error"),
            })

    def warm_node(self, node: str, limit: int = None) -> dict:
        """
        Synchronously warm ONE node (Section 10).

        Never raises for database failures: a DB error ends the
        attempt as state=failed so the READY gate stays closed
        (Section 16) and the health loop retries it later.

        Returns:
            {state: done|failed, source: hot_keys|seed,
             keys_attempted, keys_loaded,
             keys_not_owned, keys_missing, keys_write_failed,
             duration, error}
        """

        limit = self.limit if limit is None else limit

        counters = {
            "keys_attempted": 0,
            "keys_loaded": 0,
            "keys_not_owned": 0,
            "keys_missing": 0,
            "keys_write_failed": 0,
        }

        started = time.monotonic()
        error = None
        source = None

        try:

            # 1. Cluster-level hot keys: router view + fresh merge
            #    of the live nodes' Layer 6.2 rankings.
            self._merge_node_stats()

            hot_keys = self.hot_keys.top_keys(limit)

            # 1b. Layer 6.5 - cold start: NO history at all (fresh
            #     boot) means the ranking cannot say what an empty
            #     node should hold, so an optional seeder nominates
            #     a data-driven pool instead. Traffic history always
            #     wins - the moment it exists, seeding steps aside.
            candidates = hot_keys
            budget = None
            source = "hot_keys"

            if self.seeder is not None and not hot_keys:

                source = "seed"
                seeded = self.seeder.candidates()

                if seeded:
                    budget = self.seeder.budget(node)
                    candidates = [(key, 0) for key in seeded]

            # 2. One ownership snapshot for the whole loop - the
            #    live ring keeps mutating underneath (Section 10).
            #
            #    Hot keys: live ring + this node - where traffic
            #    WILL route once it rejoins (Section 10).
            #
            #    Seeds: the DECLARED membership. During a full cold
            #    start every node is gated, so the live-ring view
            #    has a single member and each node would seed the
            #    identical whole keyspace; the declared view gives
            #    each node its own distinct slice, so the cluster's
            #    unique coverage is N x the per-node budget.
            if source == "seed":
                view = self.node_manager.declared_ring_view()
            else:
                view = self.node_manager.ring_view(node)

            for key, _frequency in candidates:

                # 2b. Seeding stops at this node's budget (capacity
                #     x fill_ratio): the rest of the LRU stays free
                #     for real traffic, so a seed can never evict
                #     itself. Hot keys stay unlimited - unchanged.
                if budget is not None and counters["keys_loaded"] >= budget:
                    break

                # 3. Ownership filter: primary-only on the view.
                if view.get_node(key) != node:
                    counters["keys_not_owned"] += 1
                    continue

                counters["keys_attempted"] += 1

                # 4. Throttled database read (Sections 12-13).
                self.limiter.wait()

                value = self._db_get(key)

                # 5. Stale statistic: key deleted from the DB -> skip
                #    (Section 15 - database is the source of truth).
                if value is None:
                    counters["keys_missing"] += 1
                    continue

                # 6. Direct write: the node is not in the ring yet,
                #    and normal routing would send these keys
                #    elsewhere anyway - ownership is already checked.
                if self.cache.write_to_node(node, key, value):
                    counters["keys_loaded"] += 1
                else:
                    counters["keys_write_failed"] += 1

        except DatabaseUnavailableError as db_error:

            error = str(db_error) or "database unavailable"

        except Exception as unexpected:

            error = repr(unexpected)

        duration = time.monotonic() - started

        return {
            "state": "failed" if error else "done",
            "duration": duration,
            "error": error,
            "source": source,
            **counters,
        }

    # -----------------------------------------
    # INTERNALS
    # -----------------------------------------

    def _db_get(self, key):
        """Database read through the circuit breaker when wired."""

        if self.circuit_breaker is not None:
            return self.circuit_breaker.call(self.database.get, key)

        return self.database.get(key)

    def _merge_node_stats(self) -> int:
        """
        Fold every ACTIVE node's Layer 6.2 ranking into the cluster
        view (approach A, Section 4). The recovering node is not
        active, so its empty window never dilutes the list.
        Failures are ignored - the router's own view still works.
        """

        fetch = self.stats_fetcher or self._fetch_node_stats

        with self.node_manager.lock:
            nodes = sorted(self.node_manager.active_nodes)

        folded = 0

        for node in nodes:

            try:
                counts = fetch(node, self.merge_limit)

            except Exception:
                continue

            if counts:
                folded += self.hot_keys.merge(counts)

        return folded

    @staticmethod
    def _fetch_node_stats(node: str, limit: int):
        """GET /stats/top-keys from one node."""

        response = requests.get(
            f"{node}/stats/top-keys",
            params={"limit": limit},
            timeout=1
        )

        if response.status_code != 200:
            return None

        return response.json().get("top_keys") or []
