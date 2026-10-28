import threading
import time

import pytest
import requests
import uvicorn

from urllib.parse import quote

from cache.hot_keys import HotKeyManager
from cluster.events import LifecycleEvent
from cluster.health_monitor import run_health_pass
from cluster.node_lifecycle import on_health_result
from cluster.node_manager import NodeManager
from cluster.node_state import NodeState
from database.exceptions import DatabaseUnavailableError
from services.cache_service import CacheService
from services.warmup import CacheWarmup, WarmupRateLimiter


NODE1 = "http://localhost:8001"
NODE2 = "http://localhost:8002"
NODE3 = "http://localhost:8003"


# =========================================================
# FAKES
# =========================================================

class FakeDatabase:
    """Deterministic in-memory DB; can be switched to 'down'."""

    def __init__(self, values=None, fail=False):

        self.values = dict(values or {})
        self.fail = fail
        self.reads = []

    def get(self, key):

        self.reads.append(key)

        if self.fail:
            raise DatabaseUnavailableError("simulated outage")

        return self.values.get(key)

    def set(self, key, value):

        if self.fail:
            raise DatabaseUnavailableError("simulated outage")

        self.values[key] = value

    def delete(self, key):

        if self.fail:
            raise DatabaseUnavailableError("simulated outage")

        return self.values.pop(key, None) is not None


class FakeCache:
    """Records direct node writes; can refuse writes per node."""

    def __init__(self, fail_nodes=()):

        self.writes = []
        self.fail_nodes = set(fail_nodes)

    def write_to_node(self, node, key, value, ttl=None):

        if node in self.fail_nodes:
            return False

        self.writes.append((node, key, value))

        return True


def make_manager(
    nodes=None,
    initial_state=NodeState.READY,
    failure_threshold=3,
    recovery_threshold=2
):
    return NodeManager(
        nodes=list(nodes or []),
        virtual_nodes=10,
        failure_threshold=failure_threshold,
        recovery_threshold=recovery_threshold,
        initial_state=initial_state
    )


def healthy(manager, node, times=1):
    for _ in range(times):
        on_health_result(manager, node, True)


def unhealthy(manager, node, times=1):
    for _ in range(times):
        on_health_result(manager, node, False)


def make_warmup(
    manager,
    database=None,
    cache=None,
    hot_keys=None,
    **kwargs
):
    """CacheWarmup wired with fakes (no throttling, no network)."""

    kwargs.setdefault("rate_limit", 0)
    kwargs.setdefault("stats_fetcher", lambda node, limit: [])

    warmup = CacheWarmup(
        node_manager=manager,
        database=database if database is not None else FakeDatabase(),
        hot_key_manager=hot_keys if hot_keys is not None else HotKeyManager(),
        distributed_cache=cache if cache is not None else FakeCache(),
        **kwargs
    )

    return warmup


def owned_keys(manager, node, candidates):
    """Subset of candidates whose expected primary (view) is `node`."""

    view = manager.ring_view(node)

    return [key for key in candidates if view.get_node(key) == node]


def wait_thread(warmup, node, timeout=5):
    thread = warmup._threads.get(node)
    if thread is not None:
        thread.join(timeout=timeout)


# =========================================================
# HOT KEY MANAGER
# =========================================================

def test_hot_key_manager_record_and_top_keys():
    hot = HotKeyManager()

    for _ in range(5):
        hot.record("user:42")
    for _ in range(3):
        hot.record("product:17")
    hot.record("user:91")

    assert hot.top_keys() == [("user:42", 5), ("product:17", 3), ("user:91", 1)]
    assert hot.top_keys(limit=1) == [("user:42", 5)]
    assert hot.stats()["tracked_keys"] == 3
    assert hot.stats()["untracked"] == 0

    hot.clear()
    assert hot.top_keys() == []
    assert hot.stats() == {"tracked_keys": 0, "max_keys": 10000, "untracked": 0}


def test_hot_key_manager_record_bounded_keeps_known_keys():
    """Layer 6.2 record policy: full -> new keys untracked, known keep counting."""

    hot = HotKeyManager(max_keys=2)

    hot.record("a")
    hot.record("b")
    hot.record("c")          # refused
    hot.record("a")          # known key still counts

    stats = hot.stats()

    assert stats["tracked_keys"] == 2
    assert stats["untracked"] == 1
    assert dict(hot.top_keys()) == {"a": 2, "b": 1}


def test_hot_key_manager_merge_sums_and_trims_to_bound():
    """Batch merge: additive counts, union trimmed to max_keys by frequency."""

    hot = HotKeyManager(max_keys=3)

    hot.record("keep:1")                              # 1

    # Node snapshot shape ([[key, count], ...]) + a Counter.
    added = hot.merge([["keep:1", 9], ["node:hot", 50], ["node:warm", 2]])
    hot.merge({"router:hot": 20})

    assert added == 3
    assert dict(hot.top_keys()) == {
        "node:hot": 50,
        "router:hot": 20,
        "keep:1": 10,          # 1 + 9 - additive
    }

    # The union (4 keys) was trimmed back to max_keys=3:
    # "node:warm" (2) lost against keep:1 (10).
    assert hot.stats()["tracked_keys"] == 3
    assert hot.stats()["untracked"] == 0


def test_cache_service_records_every_key_into_hot_keys():
    """GET/SET/DELETE through the service feed the router-side ranking."""

    hot = HotKeyManager()

    service = CacheService(
        cache=FakeCacheForService(),
        database=FakeDatabase(values={"user:1": "v1"}),
        hot_keys=hot
    )

    service.set("user:1", "v1")                 # 1
    service.get("user:1")                       # 2 (hit or miss - it was accessed)
    service.get("user:1")                       # 3
    service.delete("user:1")                    # 4
    service.get("user:2")                       # 5

    assert dict(hot.top_keys()) == {"user:1": 4, "user:2": 1}


class FakeCacheForService:
    """Minimal DistributedCache stand-in for CacheService tests."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return (self.data.get(key), "node") if key in self.data else (None, None)

    def set(self, key, value, ttl=None, sequence=None):
        self.data[key] = value
        return ["node"]

    def delete(self, key, sequence=None):
        self.data.pop(key, None)
        return ["node"]


# =========================================================
# RATE LIMITER
# =========================================================

def test_rate_limiter_spaces_database_reads():
    limiter = WarmupRateLimiter(requests_per_second=20)   # 0.05s apart

    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    elapsed = time.monotonic() - start

    # 4 calls = 3 gaps of 0.05s; allow slack for scheduling.
    assert elapsed >= 0.12


def test_rate_limiter_unlimited_when_disabled():
    limiter = WarmupRateLimiter(requests_per_second=0)

    start = time.monotonic()
    for _ in range(100):
        limiter.wait()
    assert time.monotonic() - start < 0.5


def test_rate_limiter_rejects_negative():
    with pytest.raises(ValueError):
        WarmupRateLimiter(requests_per_second=-1)


# =========================================================
# OWNERSHIP FILTER (Section 10 corrected)
# =========================================================

def test_warmup_warms_only_keys_the_node_would_own():
    """Primary-only ownership on a ring view with the gated node."""

    manager = make_manager([NODE1, NODE2])
    manager.start_node(NODE3)
    manager.mark_recovering(NODE3)

    database = FakeDatabase(values={f"key:{i}": i for i in range(50)})
    cache = FakeCache()
    hot = HotKeyManager()

    candidates = [f"key:{i}" for i in range(50)]
    mine = owned_keys(manager, NODE3, candidates)
    assert mine, "test needs at least one key owned by NODE3"

    for key in candidates:
        hot.record(key)

    warmup = make_warmup(manager, database=database, cache=cache, hot_keys=hot)
    result = warmup.warm_node(NODE3)

    warmed = [key for node, key, value in cache.writes if node == NODE3]

    assert result["state"] == "done"
    assert sorted(warmed) == sorted(mine)
    assert result["keys_loaded"] == len(mine)
    assert result["keys_not_owned"] == len(candidates) - len(mine)
    assert result["keys_missing"] == 0

    # Regression for the Section 10 bug: the LIVE ring can never
    # return a gated node - that is why ownership uses the view.
    assert NODE3 not in manager.ring.get_all_nodes()
    for key in mine:
        assert manager.ring.get_node(key) != NODE3


def test_expected_primary_uses_ring_view_with_gated_node():
    manager = make_manager([NODE1, NODE2])
    manager.start_node(NODE3)

    view = manager.ring_view(NODE3)

    for i in range(30):
        key = f"k{i}"
        expected = view.get_node(key)
        assert manager.expected_primary(key, NODE3) == expected
        if expected == NODE3:
            # On the live ring the key belongs to someone else.
            assert manager.ring.get_node(key) != NODE3


# =========================================================
# WARM-UP WORK (DB -> direct write, stale stats skipped)
# =========================================================

def test_warmup_loads_from_database_and_skips_missing():
    manager = make_manager([NODE1, NODE2])
    manager.start_node(NODE3)
    manager.mark_recovering(NODE3)

    candidates = [f"key:{i}" for i in range(40)]
    mine = owned_keys(manager, NODE3, candidates)
    assert mine

    present = mine[:len(mine) // 2 or 1]
    missing = [key for key in mine if key not in present]

    database = FakeDatabase(values={key: f"v-{key}" for key in present})
    cache = FakeCache()
    hot = HotKeyManager()

    for key in mine:
        hot.record(key)

    warmup = make_warmup(manager, database=database, cache=cache, hot_keys=hot)
    result = warmup.warm_node(NODE3)

    assert result["state"] == "done"
    assert result["keys_attempted"] == len(mine)
    assert result["keys_loaded"] == len(present)
    assert result["keys_missing"] == len(missing)

    written = {key: value for node, key, value in cache.writes}

    assert sorted(written) == sorted(present)
    for key, value in written.items():
        assert value == f"v-{key}"


def test_warmup_merge_pulls_node_statistics():
    """Before warming, node /stats/top-keys rankings are folded in."""

    manager = make_manager([NODE1])
    manager.start_node(NODE2)
    manager.mark_recovering(NODE2)

    hot = HotKeyManager()
    hot.merge([["router:key", 3]])

    def fake_fetch(node, limit):
        assert node == NODE1
        return [["node:key", 7]]

    warmup = make_warmup(
        manager,
        database=FakeDatabase(values={"node:key": "from-db"}),
        cache=FakeCache(),
        hot_keys=hot,
        stats_fetcher=fake_fetch
    )

    result = warmup.warm_node(NODE2)

    assert result["state"] == "done"
    assert dict(hot.top_keys())["node:key"] == 7


# =========================================================
# DB FAILURE -> GATE BLOCKS READY -> RETRY (Section 16)
# =========================================================

def test_db_failure_blocks_ready_then_retry_recovers():
    manager = make_manager(
        [NODE1, NODE2, NODE3],
        failure_threshold=3,
        recovery_threshold=2
    )

    # Give NODE1 some hot keys it owns.
    candidates = [f"key:{i}" for i in range(40)]
    mine = owned_keys(manager, NODE1, candidates)
    assert mine

    hot = HotKeyManager()
    for key in mine:
        hot.record(key)

    database = FakeDatabase(
        values={key: "v" for key in mine},
        fail=True
    )
    cache = FakeCache()

    warmup = make_warmup(
        manager, database=database, cache=cache, hot_keys=hot
    )
    warmup.install()

    unhealthy(manager, NODE1, times=3)           # -> FAILED
    healthy(manager, NODE1)                      # -> RECOVERING (+ ensure)

    wait_thread(warmup, NODE1)

    status = warmup.status()[NODE1]

    assert status["state"] == "failed"
    assert status["error"] is not None
    assert cache.writes == []

    # Recovery threshold reached, but the warm-up gate holds the node.
    healthy(manager, NODE1)
    healthy(manager, NODE1)
    healthy(manager, NODE1)

    assert manager.get_node_state(NODE1) == "recovering"
    assert NODE1 not in manager.active_nodes
    assert not warmup.recovery_ready(NODE1)

    # Database comes back -> the next health pass retries.
    database.fail = False
    run_health_pass(manager, checker=lambda node: True)

    wait_thread(warmup, NODE1)

    status = warmup.status()[NODE1]

    assert status["state"] == "done"
    assert status["attempts"] == 2
    assert status["keys_loaded"] == len(mine)

    # Next healthy check: threshold + gate -> READY, back in the ring.
    healthy(manager, NODE1)

    assert manager.get_node_state(NODE1) == "ready"
    assert NODE1 in manager.active_nodes
    assert NODE1 in manager.ring.get_all_nodes()


def test_recovery_gate_none_keeps_plain_6_1_behavior():
    """No gate configured -> RECOVERING -> READY after the threshold."""

    manager = make_manager([NODE1], failure_threshold=3, recovery_threshold=2)

    unhealthy(manager, NODE1, times=3)           # -> FAILED
    healthy(manager, NODE1)                      # -> RECOVERING
    healthy(manager, NODE1)
    healthy(manager, NODE1)                      # -> READY

    assert manager.get_node_state(NODE1) == "ready"


def test_failed_writes_still_complete_warmup():
    """A dead target node: writes fail, but the attempt is 'done' -
    the health monitor will fail the node separately (Section 18)."""

    manager = make_manager([NODE1])
    manager.start_node(NODE2)
    manager.mark_recovering(NODE2)

    mine = owned_keys(manager, NODE2, [f"key:{i}" for i in range(30)])
    assert mine

    hot = HotKeyManager()
    for key in mine:
        hot.record(key)

    cache = FakeCache(fail_nodes={NODE2})

    warmup = make_warmup(
        manager,
        database=FakeDatabase(values={key: "v" for key in mine}),
        cache=cache,
        hot_keys=hot
    )

    result = warmup.warm_node(NODE2)

    assert result["state"] == "done"
    assert result["keys_write_failed"] == len(mine)
    assert result["keys_loaded"] == 0


# =========================================================
# EVENTS (post-lock delivery)
# =========================================================

def test_listener_fires_after_lock_is_released():
    """A listener must be able to take the manager lock from another
    thread - i.e. it cannot be running while the lifecycle holds it."""

    manager = make_manager([NODE1])

    events = []
    lock_free = []

    def listener(event):

        done = threading.Event()

        def probe():
            with manager.lock:
                pass
            done.set()

        threading.Thread(target=probe, daemon=True).start()

        lock_free.append(done.wait(timeout=1.0))
        events.append(event)

    manager.add_listener(listener)

    manager.start_node(NODE2)                    # -> STARTING (register: no event)
    healthy(manager, NODE2)                      # -> RECOVERING
    healthy(manager, NODE2)
    healthy(manager, NODE2)                      # -> READY
    manager.mark_failed(NODE2)                   # -> FAILED

    assert all(lock_free), "listener ran while the lifecycle lock was held"

    delivered = [(e.node, e.old, e.new) for e in events]

    assert delivered == [
        (NODE2, "starting", "recovering"),
        (NODE2, "recovering", "ready"),
        (NODE2, "ready", "failed"),
    ]

    assert all(isinstance(e, LifecycleEvent) for e in events)


def test_listener_errors_do_not_break_the_lifecycle():
    manager = make_manager([NODE1])

    def bad_listener(event):
        raise RuntimeError("boom")

    manager.add_listener(bad_listener)

    healthy(manager, NODE1)                      # ready -> ready (no event)

    manager.mark_failed(NODE1)                   # fires the bad listener

    assert manager.get_node_state(NODE1) == "failed"


def test_starting_event_resets_warmup_status():
    """An explicit restart forgets the previous warm-up record."""

    manager = make_manager([NODE1], failure_threshold=3)
    warmup = make_warmup(manager)
    warmup.install()

    unhealthy(manager, NODE1, times=3)           # -> FAILED
    healthy(manager, NODE1)                      # -> RECOVERING + ensure
    wait_thread(warmup, NODE1)

    assert NODE1 in warmup.status()

    unhealthy(manager, NODE1, times=3)           # RECOVERING -> FAILED
    manager.start_node(NODE1)                    # FAILED -> STARTING

    assert manager.get_node_state(NODE1) == "starting"
    assert NODE1 not in warmup.status()


# =========================================================
# TRIGGER / DEDUPE / DISABLED
# =========================================================

def test_double_ensure_runs_warmup_once():
    manager = make_manager([NODE1])
    manager.start_node(NODE2)
    manager.mark_recovering(NODE2)

    warmup = make_warmup(manager)

    release = threading.Event()
    calls = []

    def slow_warm(node, limit=None):
        calls.append(node)
        release.wait(timeout=3)
        return {"state": "done", "keys_attempted": 0, "keys_loaded": 0}

    warmup.warm_node = slow_warm

    assert warmup.ensure(NODE2) is True
    assert warmup.ensure(NODE2) is False         # deduped
    assert warmup.ensure(NODE2) is False

    release.set()
    wait_thread(warmup, NODE2)

    assert calls == [NODE2]
    assert warmup.status()[NODE2]["state"] == "done"

    # Once done, ensure stays a no-op.
    assert warmup.ensure(NODE2) is False


def test_disabled_warmup_gates_nothing():
    manager = make_manager([NODE1], failure_threshold=3, recovery_threshold=2)
    warmup = make_warmup(manager, enabled=False)
    warmup.install()

    unhealthy(manager, NODE1, times=3)           # -> FAILED
    healthy(manager, NODE1)                      # -> RECOVERING

    assert warmup.ensure(NODE1) is False
    assert warmup.status()[NODE1]["state"] == "disabled"
    assert warmup.recovery_ready(NODE1) is True

    healthy(manager, NODE1)
    healthy(manager, NODE1)                      # -> READY without warming

    assert manager.get_node_state(NODE1) == "ready"


def test_recovering_without_record_is_ungated():
    """A RECOVERING node with no warm-up record keeps 6.1 behavior."""

    warmup = make_warmup(make_manager([NODE1]))

    assert warmup.recovery_ready(NODE1) is True


# =========================================================
# COLD-START SEEDING (Layer 6.5)
# =========================================================

class FakeSeeder:
    """CacheSeeder stand-in: fixed pool, fixed budget, call counting."""

    def __init__(self, keys=(), budget=10, fail=False):

        self.keys = list(keys)
        self.budget_value = budget
        self.fail = fail

        self.candidate_calls = 0
        self.budget_calls = []

    def candidates(self):

        self.candidate_calls += 1

        if self.fail:
            raise DatabaseUnavailableError("seed selection failed")

        return list(self.keys)

    def budget(self, node):

        self.budget_calls.append(node)

        return self.budget_value


def test_cold_start_seeds_when_the_ranking_is_empty():
    """Fresh boot: no history -> the seeder's pool, capped by budget."""

    manager = make_manager([NODE1, NODE2])
    manager.start_node(NODE3)
    manager.mark_recovering(NODE3)

    candidates = [f"seed:{i}" for i in range(60)]
    mine = owned_keys(manager, NODE3, candidates)
    assert len(mine) > 5

    database = FakeDatabase(values={key: f"v-{key}" for key in candidates})
    cache = FakeCache()
    seeder = FakeSeeder(keys=candidates, budget=5)

    warmup = make_warmup(
        manager, database=database, cache=cache, seeder=seeder
    )
    result = warmup.warm_node(NODE3)

    warmed = [key for node, key, value in cache.writes if node == NODE3]

    assert result["state"] == "done"
    assert result["source"] == "seed"

    # The budget binds: exactly 5 loaded, walked in pool order,
    # every one of them owned by this node.
    assert result["keys_loaded"] == 5
    assert result["keys_attempted"] == 5
    assert warmed == mine[:5]

    # The budget was asked about THIS node, and only after the
    # pool turned out non-empty.
    assert seeder.budget_calls == [NODE3]
    assert seeder.candidate_calls == 1


def test_full_cold_start_seeds_distinct_slices_per_node():
    """Whole cluster booting (every node gated): the pool is still
    split by DECLARED membership.

    With the live-ring view alone each node would be the ring's
    only member and seed the IDENTICAL top keys - the cluster's
    unique coverage would stay at one node's budget. The declared
    view gives each node its own slice: N x the coverage.
    """

    manager = make_manager(
        [NODE1, NODE2, NODE3], initial_state=NodeState.STARTING
    )

    # Nobody is routable yet - the true cold-start condition.
    assert not manager.active_nodes

    candidates = [f"seed:{i}" for i in range(180)]
    database = FakeDatabase(values={key: f"v-{key}" for key in candidates})

    slices = {}

    for node in (NODE1, NODE2, NODE3):

        cache = FakeCache()
        seeder = FakeSeeder(keys=candidates, budget=15)

        warmup = make_warmup(
            manager, database=database, cache=cache, seeder=seeder
        )
        result = warmup.warm_node(node)

        assert result["state"] == "done"
        assert result["source"] == "seed"
        assert result["keys_loaded"] == 15
        # The walk skips the keys the other nodes own.
        assert result["keys_not_owned"] > 0

        slices[node] = {key for n, key, v in cache.writes if n == node}

    # Disjoint slices that together cover 3 x the per-node budget.
    assert slices[NODE1] & slices[NODE2] == set()
    assert slices[NODE1] & slices[NODE3] == set()
    assert slices[NODE2] & slices[NODE3] == set()
    assert len(slices[NODE1] | slices[NODE2] | slices[NODE3]) == 45


def test_seeding_steps_aside_when_traffic_history_exists():
    """Any ranking at all -> hot keys win; the seeder is never asked."""

    manager = make_manager([NODE1])
    manager.start_node(NODE2)
    manager.mark_recovering(NODE2)

    # A key NODE2 actually owns, so the load is observable.
    view = manager.ring_view(NODE2)
    key = next(
        f"k{i}" for i in range(100)
        if view.get_node(f"k{i}") == NODE2
    )

    hot = HotKeyManager()
    hot.record(key)
    hot.record(key)

    database = FakeDatabase(values={key: "from-db"})
    cache = FakeCache()
    seeder = FakeSeeder(keys=["seed:1", "seed:2"], budget=1)

    warmup = make_warmup(
        manager, database=database, cache=cache,
        hot_keys=hot, seeder=seeder
    )
    result = warmup.warm_node(NODE2)

    assert result["state"] == "done"
    assert result["source"] == "hot_keys"
    assert result["keys_loaded"] == 1

    warmed = [k for node, k, value in cache.writes if node == NODE2]
    assert warmed == [key]

    # History wins so decisively the seeder is never consulted.
    assert seeder.candidate_calls == 0
    assert seeder.budget_calls == []


def test_wiring_no_seeder_keeps_the_old_cold_start():
    """No seeder -> an empty ranking still loads nothing (unchanged)."""

    manager = make_manager([NODE1])
    manager.start_node(NODE2)
    manager.mark_recovering(NODE2)

    warmup = make_warmup(
        manager,
        database=FakeDatabase(values={"k0": "v"}),
        cache=FakeCache()
    )
    result = warmup.warm_node(NODE2)

    assert result["state"] == "done"
    assert result["source"] == "hot_keys"
    assert result["keys_attempted"] == 0
    assert result["keys_loaded"] == 0


def test_stale_seed_rows_are_skipped_not_failed():
    """Pool built, rows deleted since -> missing, not an error (Section 15)."""

    manager = make_manager([NODE1, NODE2])
    manager.start_node(NODE3)
    manager.mark_recovering(NODE3)

    candidates = [f"seed:{i}" for i in range(30)]
    mine = owned_keys(manager, NODE3, candidates)
    assert mine

    # The whole database is empty "now" - every seeded key went away.
    seeder = FakeSeeder(keys=candidates, budget=100)
    cache = FakeCache()

    warmup = make_warmup(
        manager, database=FakeDatabase(values={}),
        cache=cache, seeder=seeder
    )
    result = warmup.warm_node(NODE3)

    assert result["state"] == "done"
    assert result["source"] == "seed"
    assert result["keys_loaded"] == 0
    assert result["keys_missing"] == len(mine)
    assert cache.writes == []


def test_seed_selection_failure_fails_the_attempt():
    """A DB outage during seeding behaves like any warm-up DB failure."""

    manager = make_manager([NODE1])
    manager.start_node(NODE2)
    manager.mark_recovering(NODE2)

    cache = FakeCache()
    seeder = FakeSeeder(fail=True)

    warmup = make_warmup(
        manager, database=FakeDatabase(), cache=cache, seeder=seeder
    )
    result = warmup.warm_node(NODE2)

    # Failed -> the READY gate holds and the health loop retries,
    # exactly like a failed hot-key read (Section 16).
    assert result["state"] == "failed"
    assert result["error"] == "seed selection failed"
    assert result["source"] == "seed"
    assert cache.writes == []


def test_zero_seed_budget_loads_nothing():
    """fill_ratio 0 (or a capacity of 0) -> seeds nothing, still done."""

    manager = make_manager([NODE1, NODE2])
    manager.start_node(NODE3)
    manager.mark_recovering(NODE3)

    candidates = [f"seed:{i}" for i in range(30)]
    mine = owned_keys(manager, NODE3, candidates)
    assert mine

    seeder = FakeSeeder(keys=candidates, budget=0)
    cache = FakeCache()

    warmup = make_warmup(
        manager, database=FakeDatabase(values={"seed:0": "v"}),
        cache=cache, seeder=seeder
    )
    result = warmup.warm_node(NODE3)

    assert result["state"] == "done"
    assert result["source"] == "seed"
    assert result["keys_attempted"] == 0
    assert result["keys_loaded"] == 0
    assert cache.writes == []
    # Budget was still resolved for the node (bounded decision made
    # before the first write).
    assert seeder.budget_calls == [NODE3]


# =========================================================
# ROUTER ENDPOINTS (Layer 6.3)
# =========================================================

def _start_server(app, port):
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error"
    )

    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)

    raise RuntimeError(f"Server on port {port} failed to start")


def _stop_servers(servers):
    for server in servers:
        server.should_exit = True
    time.sleep(0.3)


def test_router_hot_keys_and_warmup_endpoints():
    """GET /cluster/hot-keys merges node stats; warm-up status + trigger."""

    import main as cache_node_module
    import router.router as router_module

    from cache import CacheStatistics, LRUCache

    base = 20000
    node_port = base + 1
    router_port = base + 10
    node_url = f"http://127.0.0.1:{node_port}"

    # Isolate from the module-level objects other tests share.
    original_cache = cache_node_module.cache
    original_statistics = cache_node_module.statistics

    cache_node_module.statistics = CacheStatistics(max_tracked_keys=100)
    cache_node_module.cache = LRUCache(
        capacity=10,
        on_eviction=cache_node_module.statistics.record_eviction
    )

    node_server = _start_server(cache_node_module.app, node_port)

    try:
        router_module.configure_cluster([node_url], replication_factor=1)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            # Traffic on the node creates a Layer 6.2 window.
            for _ in range(3):
                requests.put(
                    f"{node_url}/cache/user:42",
                    json={"value": "hot", "ttl": 60},
                    timeout=3
                )
                requests.get(f"{node_url}/cache/user:42", timeout=3)

            # Traffic through the router feeds the router-side view.
            assert requests.put(
                f"{router_url}/cache/router:key",
                json={"value": 1, "ttl": 60},
                timeout=3
            ).status_code == 200

            hot = requests.get(
                f"{router_url}/cluster/hot-keys?limit=5&per_node=10",
                timeout=5
            )

            assert hot.status_code == 200

            body = hot.json()

            ranking = dict(body["top_keys"])

            assert ranking["router:key"] >= 1
            assert ranking["user:42"] >= 3          # merged from the node
            assert body["nodes_queried"] == 1
            # 3 direct GETs on the node = 3 hits in its window.
            assert body["per_node"][node_url]["top_keys"] == [["user:42", 3]]
            assert body["max_keys"] == 10000

            status = requests.get(f"{router_url}/cluster/warmup", timeout=3)

            assert status.status_code == 200

            status_body = status.json()

            assert status_body["enabled"] is True
            assert status_body["limit"] == 1000
            assert status_body["rate_limit"] == 100
            assert status_body["nodes"] == {}

            # Manual trigger against a known node (path-converter URL).
            trigger = requests.post(
                f"{router_url}/cluster/warmup/{quote(node_url, safe='')}",
                timeout=5
            )

            assert trigger.status_code == 200

            trigger_body = trigger.json()

            assert trigger_body["node"] == node_url
            assert trigger_body["started"] is True

            # Wait for the background attempt to finish.
            for _ in range(50):
                state = requests.get(
                    f"{router_url}/cluster/warmup", timeout=3
                ).json()["nodes"].get(node_url, {}).get("state")

                if state == "done":
                    break
                time.sleep(0.1)

            assert state == "done"

            # Unknown node -> 404.
            missing = requests.post(
                f"{router_url}/cluster/warmup/{quote('http://nope', safe='')}",
                timeout=3
            )

            assert missing.status_code == 404

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers([node_server])
        cache_node_module.cache = original_cache
        cache_node_module.statistics = original_statistics
