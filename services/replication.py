import threading
import time

from collections import deque

from database.exceptions import DatabaseUnavailableError


class ReplicationManager:
    """
    Layer 7.3/7.5/7.6 - router-side replication coordinator.

    The router is the single authority for write ordering. Every
    mutation (SET or DELETE) is assigned a monotonically increasing
    sequence number, fanned out to the primary + replicas with that
    sequence attached, recorded in a bounded in-memory replication
    log, and acknowledged per node.

        SET/DELETE -> next_sequence()
                   -> fan-out (DistributedCache) carrying the sequence
                   -> record(key, op, value, ttl, sequence)
                   -> ack(node, sequence) for every node that accepted

    A node that fell behind (restart, brief outage) is brought back
    with sync_node():

        small gap + log intact  -> replay entries > acked (incremental)
        large gap / log rolled  -> snapshot: DB pull, ownership-filtered

    Because the router owns ordering and nodes never talk to each
    other, replication loops are structurally impossible and there
    is no split-brain: one coordinator, one sequence.

    Status states mirror warm-up: pending -> running -> done|failed
    (+ disabled). Only ensure()/worker mutate status (single writer).
    """

    def __init__(
        self,
        node_manager,
        distributed_cache,
        database,
        log_size: int = 10000,
        snapshot_threshold: int = 1000,
        enabled: bool = True
    ):

        self.node_manager = node_manager
        self.cache = distributed_cache
        self.database = database

        self.log_size = log_size
        self.snapshot_threshold = snapshot_threshold
        self.enabled = bool(enabled)

        self._lock = threading.Lock()

        # Cluster-wide monotonic write order. Starts at 0; the first
        # mutation gets sequence 1.
        self._sequence = 0

        # Bounded replication log, oldest -> newest.
        self._log = deque(maxlen=log_size)

        # node -> highest sequence that node has acknowledged.
        self._acked = {}

        # node -> sync status dict (single writer: ensure/_run).
        self._status = {}

        # node -> worker thread (joinable for tests/demos).
        self._threads = {}

        # Last observed failover (Layer 7.5).
        self._last_failover = None

        # Bounded failover history, newest last (Layer 7.5).
        self._failovers = deque(maxlen=50)

        self._installed = False

    # --------------------------------------------------
    # SEQUENCE + LOG + ACK (Layer 7.3 core)
    # --------------------------------------------------

    def next_sequence(self) -> int:
        """Allocate the next cluster-wide write sequence."""

        with self._lock:

            self._sequence += 1

            return self._sequence

    def current_sequence(self) -> int:
        """The most recent sequence handed out (0 = no writes yet)."""

        with self._lock:
            return self._sequence

    def record(self, key: str, op: str, value, ttl, sequence: int) -> None:
        """Append one mutation to the bounded replication log."""

        with self._lock:

            self._log.append({
                "sequence": sequence,
                "key": key,
                "op": op,
                "value": value,
                "ttl": ttl
            })

            # Keep the counter consistent with the log: an
            # out-of-band record must never leave current_sequence()
            # behind an entry the log already covers.  No-op in the
            # normal flow where next_sequence() allocated it.
            if sequence is not None and sequence > self._sequence:
                self._sequence = sequence

    def ack(self, node: str, sequence: int) -> None:
        """Mark `node` as having applied through `sequence`."""

        with self._lock:

            if sequence > self._acked.get(node, 0):
                self._acked[node] = sequence

    def acked_sequence(self, node: str) -> int:
        """Highest sequence `node` applied (0 = never acked)."""

        with self._lock:
            return self._acked.get(node, 0)

    def forget_node(self, node: str) -> None:
        """Drop ack + status for a node (explicit restart)."""

        with self._lock:

            self._acked.pop(node, None)

        with self._lock:
            self._status.pop(node, None)

    def entries_since(self, after_sequence: int) -> list:
        """
        Log entries with sequence > `after_sequence`, oldest first.

        An empty list when the log no longer covers the gap (rolled
        over) - callers treat that as "snapshot instead".
        """

        with self._lock:
            return [
                dict(entry)
                for entry in self._log
                if entry["sequence"] > after_sequence
            ]

    def log_oldest_sequence(self):
        """Oldest sequence still in the log (None = empty log)."""

        with self._lock:
            return self._log[0]["sequence"] if self._log else None

    # --------------------------------------------------
    # WIRING (sync on RECOVERING, retry, READY gate)
    # --------------------------------------------------

    def install(self) -> bool:
        """
        Wire the replication sync into the node manager (idempotent):

            lifecycle listener -> start sync on RECOVERING,
                                  forget node on explicit restart
            health hook        -> retry failed syncs each pass

        The READY gate itself is composed by the caller (it must
        require BOTH warm-up done AND sync done).
        """

        with self._lock:

            if self._installed:
                return False

            self._installed = True

        self.node_manager.add_listener(self._on_lifecycle)
        self.node_manager.add_health_hook(self._retry_recovering)

        return True

    def _on_lifecycle(self, event) -> None:
        """Lifecycle listener (post-lock, so HTTP/DB is safe here)."""

        from cluster.node_state import NodeState

        if event.new_state is NodeState.RECOVERING:
            self.ensure(event.node)

        elif event.new_state is NodeState.STARTING:
            # Explicit restart: this is a fresh process - forget
            # whatever it had acked and start over on RECOVERING.
            self.forget_node(event.node)

        elif event.new_state is NodeState.FAILED:
            # Layer 7.5/7.6 - a node left the ring involuntarily.
            # Record the failover, then restore the redundancy it
            # carried (fire-and-forget so the health loop is not
            # blocked by a full DB pass).
            self.record_failover(event.node)
            self.repair()

    def _retry_recovering(self, manager) -> None:
        """Health-pass hook: retry syncs that failed while gated."""

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

    def sync_ready(self, node: str) -> bool:
        """
        The Layer 7 half of the recovery gate: True when `node` has
        caught up (or sync is disabled / never asked).
        """

        if not self.enabled:
            return True

        with self._lock:
            entry = self._status.get(node)

        if entry is None:
            return True

        return entry["state"] in ("done", "disabled")

    # --------------------------------------------------
    # SYNC (Layer 7.3 engine)
    # --------------------------------------------------

    def ensure(self, node: str) -> bool:
        """
        Start a sync for `node` unless one is already pending,
        running or done. Returns True when a new attempt started.
        """

        if not self.enabled:

            with self._lock:
                self._status[node] = self._entry("disabled")

            return False

        with self._lock:

            entry = self._status.get(node)

            if entry and entry["state"] in ("pending", "running", "done"):
                return False

            attempts = (entry or {}).get("attempts", 0) + 1

            self._status[node] = self._entry("pending", attempts=attempts)

        try:

            thread = threading.Thread(
                target=self._run,
                args=(node,),
                daemon=True,
                name=f"replication-sync-{attempts}"
            )

            with self._lock:
                self._threads[node] = thread

            thread.start()

        except Exception as error:

            with self._lock:
                self._status[node] = self._entry(
                    "failed", attempts=attempts, error=repr(error)
                )

            return False

        return True

    def _run(self, node: str) -> None:
        """Thread body: running -> sync_node -> done/failed."""

        with self._lock:

            entry = self._status.get(node) or self._entry("running")

            entry["state"] = "running"
            entry["started_at"] = time.time()

        try:

            result = self.sync_node(node)

        except Exception as error:

            result = {
                "state": "failed",
                "mode": None,
                "error": repr(error)
            }

        with self._lock:

            entry = self._status.get(node) or self._entry(
                result.get("state", "failed")
            )

            entry.update({
                "state": result.get("state", "failed"),
                "mode": result.get("mode"),
                "entries_replayed": result.get("entries_replayed", 0),
                "keys_loaded": result.get("keys_loaded", 0),
                "finished_at": time.time(),
                "duration": result.get("duration"),
                "error": result.get("error"),
            })

    def sync_node(self, node: str) -> dict:
        """
        Synchronously bring ONE node's data current (Layer 7.3).

        Chooses the cheaper correct strategy:

            node unknown / gap > threshold / log rolled over
                -> SNAPSHOT: DB pull, ownership-filtered, direct write
            otherwise
                -> INCREMENTAL: replay log entries the node is missing

        Never raises for database failures: a DB error ends the
        attempt as state=failed so the READY gate stays closed and
        the health loop retries later.

        Returns:
            {state: done|failed, mode: incremental|snapshot,
             entries_replayed, keys_loaded, duration, error}
        """

        started = time.monotonic()
        error = None
        mode = None
        entries_replayed = 0
        keys_loaded = 0

        try:

            acked = self.acked_sequence(node)
            current = self.current_sequence()

            if self._needs_snapshot(node, acked, current):

                mode = "snapshot"
                keys_loaded = self._snapshot_node(node)

            else:

                mode = "incremental"
                entries_replayed = self._replay_into(node, acked)

            # The node is now current through `current`.
            self.ack(node, current)

        except DatabaseUnavailableError as db_error:

            error = str(db_error) or "database unavailable"

        except Exception as unexpected:

            error = repr(unexpected)

        duration = time.monotonic() - started

        return {
            "state": "failed" if error else "done",
            "mode": mode,
            "entries_replayed": entries_replayed,
            "keys_loaded": keys_loaded,
            "duration": duration,
            "error": error,
        }

    def _needs_snapshot(self, node: str, acked: int, current: int) -> bool:
        """
        Snapshot when we cannot cheaply prove the node is only a
        little behind:

            - never acked (fresh / restarted process)
            - gap exceeds the threshold
            - the log no longer reaches back to acked+1 (rolled over)
        """

        if acked <= 0:
            return True

        if current - acked > self.snapshot_threshold:
            return True

        oldest = self.log_oldest_sequence()

        if oldest is not None and oldest > acked + 1:
            return True

        return False

    def _replay_into(self, node: str, after_sequence: int) -> int:
        """
        Incremental sync: push every logged mutation the node has
        not yet applied. Returns the number of entries replayed.
        """

        entries = self.entries_since(after_sequence)

        replayed = 0

        for entry in entries:

            if entry["op"] == "delete":

                ok = self.cache.delete_from_node(node, entry["key"])

            else:

                ok = self.cache.write_to_node(
                    node,
                    entry["key"],
                    entry["value"],
                    ttl=entry["ttl"],
                    sequence=entry["sequence"]
                )

            if ok:
                replayed += 1

        return replayed

    def _snapshot_node(self, node: str) -> int:
        """
        Snapshot sync: pull every key `node` owns (per the ring
        view that includes it) from the database and write it
        directly. The database is the source of truth, so this is
        always correct; it is simply the expensive path.
        """

        view = self.node_manager.ring_view(node)

        loaded = 0

        for key in self.database.iter_keys():

            if view.get_node(key) != node:
                continue

            value = self.database.get(key)

            if value is None:
                continue

            if self.cache.write_to_node(node, key, value):
                loaded += 1

        return loaded

    # --------------------------------------------------
    # RE-REPLICATION (Layer 7.6)
    # --------------------------------------------------

    def repair_active_nodes(self, limit: int | None = None) -> dict:
        """
        Layer 7.6 - restore full redundancy after a node left the ring.

        Iterates the database once. For every key it computes the
        CURRENT owners (the live ring already excludes FAILED nodes)
        and writes the value to each of them. Idempotent - a node
        that already holds the key simply overwrites it. Bounded by
        `limit` keys when given.

        Triggered automatically on FAILED (fire-and-forget thread)
        and available directly for tests/demos.
        """

        started = time.monotonic()

        if not self.enabled:

            return {
                "state": "disabled",
                "nodes": {},
                "keys_loaded": 0,
                "duration": 0.0
            }

        with self.node_manager.lock:
            active = sorted(self.node_manager.active_nodes)

        per_node = {node: 0 for node in active}
        total = 0

        try:

            for key in self.database.iter_keys():

                if limit is not None and total >= limit:
                    break

                value = self.database.get(key)

                if value is None:
                    continue

                # Live ring = current owners (failed node already out).
                owners = self.node_manager.get_nodes(key)

                for node in owners:

                    if node not in per_node:
                        continue

                    if self.cache.write_to_node(node, key, value):
                        per_node[node] += 1
                        total += 1

        except DatabaseUnavailableError as db_error:

            return {
                "state": "failed",
                "error": str(db_error) or "database unavailable",
                "nodes": per_node,
                "keys_loaded": total,
                "duration": time.monotonic() - started
            }

        return {
            "state": "done",
            "nodes": per_node,
            "keys_loaded": total,
            "duration": time.monotonic() - started
        }

    def repair(self) -> bool:
        """
        Fire-and-forget repair on a daemon thread (called from the
        FAILED lifecycle hook, which must not block the health loop).
        """

        if not self.enabled:
            return False

        thread = threading.Thread(
            target=self.repair_active_nodes,
            daemon=True,
            name="replication-repair"
        )

        thread.start()

        return True

    # --------------------------------------------------
    # STATUS (Layer 7.5/7.6 observability)
    # --------------------------------------------------

    def status(self) -> dict:
        """Snapshot of replication state for the status endpoint."""

        with self._lock:

            current = self._sequence
            acked = dict(self._acked)
            log_size = len(self._log)
            sync_status = {
                node: dict(entry)
                for node, entry in self._status.items()
            }
            last_failover = (
                dict(self._last_failover)
                if self._last_failover is not None
                else None
            )
            failovers = [dict(entry) for entry in self._failovers]

        # Which nodes are routable, so "behind" is meaningful.
        with self.node_manager.lock:
            active = set(self.node_manager.active_nodes)

        nodes = {}

        for node in sorted(
            set(list(acked) + list(sync_status) + list(active))
        ):

            node_ack = acked.get(node, 0)

            nodes[node] = {
                "acked_sequence": node_ack,
                "behind": max(0, current - node_ack),
                "active": node in active,
                "sync": sync_status.get(node, {}).get("state"),
            }

        return {
            "enabled": self.enabled,
            "current_sequence": current,
            "log_size": log_size,
            "log_capacity": self.log_size,
            "snapshot_threshold": self.snapshot_threshold,
            "nodes": nodes,
            "last_failover": last_failover,
            "failovers": failovers,
            "syncs": sync_status,
        }

    def record_failover(self, failed_node: str) -> None:
        """Layer 7.5 - note that a failover just happened."""

        with self._lock:

            entry = {
                "node": failed_node,
                "at": time.time(),
                "sequence": self._sequence,
            }

            self._last_failover = entry

            self._failovers.append(dict(entry))

    def last_failover(self):
        with self._lock:
            return (
                dict(self._last_failover)
                if self._last_failover is not None
                else None
            )

    def failovers(self) -> list:
        """Bounded failover history, newest last (Layer 7.5)."""

        with self._lock:
            return [dict(entry) for entry in self._failovers]

    # --------------------------------------------------
    # INTERNALS
    # --------------------------------------------------

    def _entry(self, state, attempts=0, **extra) -> dict:

        entry = {
            "state": state,
            "attempts": attempts,
            "mode": None,
            "entries_replayed": 0,
            "keys_loaded": 0,
            "started_at": None,
            "finished_at": None,
            "duration": None,
            "error": None,
        }

        entry.update(extra)

        return entry
