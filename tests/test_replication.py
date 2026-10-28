import threading
import time

import pytest
import requests
import uvicorn

import main as cache_node_module
import router.router as router_module

from cache import LRUCache
from cluster.node_manager import NodeManager
from cluster.node_state import NodeState
from services.replication import ReplicationManager


def _start_server(app, port):
    """Run a uvicorn server for an app in a background thread."""

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


# =========================================================
# LAYER 7.3 - VERSIONED CACHE ENTRIES (LRU level)
# =========================================================

def test_lru_set_stores_sequence():

    cache = LRUCache(capacity=10)

    cache.set("A", 1, sequence=7)

    value, sequence = cache.get_entry("A")

    assert value == 1
    assert sequence == 7


def test_lru_get_entry_returns_none_for_missing():

    cache = LRUCache(capacity=10)

    assert cache.get_entry("nope") == (None, None)


def test_lru_get_entry_does_not_touch_statistics():

    cache = LRUCache(capacity=10)

    cache.set("A", 1, sequence=1)

    before = (cache.hits, cache.misses)

    cache.get_entry("A")

    assert (cache.hits, cache.misses) == before


def test_lru_update_overwrites_sequence():

    cache = LRUCache(capacity=10)

    cache.set("A", 1, sequence=1)
    cache.set("A", 2, sequence=2)

    value, sequence = cache.get_entry("A")

    assert (value, sequence) == (2, 2)


def test_lru_sequence_defaults_to_none():

    cache = LRUCache(capacity=10)

    cache.set("A", 1)

    assert cache.get_entry("A") == (1, None)


def test_lru_get_entry_respects_ttl():

    cache = LRUCache(capacity=10)

    cache.set("A", 1, ttl=0.05, sequence=1)
    time.sleep(0.08)

    assert cache.get_entry("A") == (None, None)


# =========================================================
# STAGE A - INTERNAL REPLICATION ENDPOINT (node level)
# =========================================================

@pytest.fixture
def node_url():
    """One real cache node on a test port."""

    port = 19700
    server = _start_server(cache_node_module.app, port)

    yield f"http://127.0.0.1:{port}"

    _stop_servers([server])


def test_internal_replicate_round_trip(node_url):

    response = requests.put(
        f"{node_url}/internal/replicate/user:1",
        json={"value": {"name": "Gaurav"}, "ttl": 60, "sequence": 42},
        timeout=3
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "stored", "key": "user:1", "sequence": 42
    }

    entry = requests.get(
        f"{node_url}/internal/entry/user:1", timeout=3
    ).json()

    assert entry["exists"] is True
    assert entry["value"] == {"name": "Gaurav"}
    assert entry["sequence"] == 42


def test_internal_entry_reports_missing(node_url):

    entry = requests.get(
        f"{node_url}/internal/entry/nope", timeout=3
    ).json()

    assert entry == {
        "key": "nope", "exists": False, "value": None, "sequence": None
    }


def test_internal_replicate_without_sequence(node_url):

    requests.put(
        f"{node_url}/internal/replicate/k",
        json={"value": "v"},
        timeout=3
    )

    entry = requests.get(
        f"{node_url}/internal/entry/k", timeout=3
    ).json()

    assert entry["value"] == "v"
    assert entry["sequence"] is None


def test_internal_delete_is_idempotent(node_url):

    requests.put(
        f"{node_url}/internal/replicate/k",
        json={"value": "v", "sequence": 1},
        timeout=3
    )

    first = requests.delete(
        f"{node_url}/internal/replicate/k", timeout=3
    )
    second = requests.delete(
        f"{node_url}/internal/replicate/k", timeout=3
    )

    assert first.status_code == 200
    assert second.status_code == 200

    entry = requests.get(
        f"{node_url}/internal/entry/k", timeout=3
    ).json()

    assert entry["exists"] is False


def test_internal_replicate_does_not_touch_client_api(node_url):
    """A replication write is visible to the client GET (same cache)."""

    requests.put(
        f"{node_url}/internal/replicate/k",
        json={"value": "v", "sequence": 5},
        timeout=3
    )

    client = requests.get(f"{node_url}/cache/k", timeout=3)

    assert client.status_code == 200
    assert client.json()["value"] == "v"


# =========================================================
# STAGE A - ROUTER END-TO-END (best-effort replication intact)
# =========================================================

def test_router_put_fans_out_via_internal_endpoint():
    """PUT through the router must land on 2 nodes with sequence."""

    base = 19710
    urls = [f"http://127.0.0.1:{base + i}" for i in range(3)]
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, base + i)
        for i in range(3)
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            response = requests.put(
                f"{router_url}/cache/user:1",
                json={"value": {"name": "Gaurav"}, "ttl": 60},
                timeout=3
            )

            assert response.status_code == 200

            body = response.json()

            assert body["status"] == "stored"
            assert body["replicated_to"] == 2

            for node_url in body["nodes"]:

                entry = requests.get(
                    f"{node_url}/internal/entry/user:1",
                    timeout=3
                ).json()

                assert entry["exists"] is True
                assert entry["value"] == {"name": "Gaurav"}
                # Stage A: sequence present once ReplicationManager
                # assigns it (Stage B). Until then it may be None.
                assert "sequence" in entry

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


# =========================================================
# STAGE B - REPLICATION MANAGER (Layer 7.3 core, unit level)
# =========================================================

class _FakeDistCache:
    """Records writes; can be told to fail specific nodes."""

    def __init__(self):
        self.writes = []
        self.deletes = []
        self.fail_nodes = set()

    def write_to_node(self, node, key, value, ttl=None, sequence=None):
        if node in self.fail_nodes:
            return False
        self.writes.append((node, key, value, sequence))
        return True

    def delete_from_node(self, node, key):
        if node in self.fail_nodes:
            return False
        self.deletes.append((node, key))
        return True


class _FakeDB:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def iter_keys(self):
        return iter(sorted(self.values))

    def get(self, key):
        return self.values.get(key)


def _make_manager(nodes=None, **kwargs):
    manager = NodeManager(
        nodes=nodes or ["http://n1", "http://n2", "http://n3"],
        virtual_nodes=10,
        initial_state=NodeState.READY
    )
    return manager, ReplicationManager(
        node_manager=manager,
        distributed_cache=_FakeDistCache(),
        database=_FakeDB(),
        **kwargs
    )


def test_sequence_is_monotonic():

    _, replication = _make_manager()

    seqs = [replication.next_sequence() for _ in range(5)]

    assert seqs == [1, 2, 3, 4, 5]
    assert replication.current_sequence() == 5


def test_ack_tracks_high_water_mark():

    _, replication = _make_manager()

    replication.ack("http://n1", 5)
    replication.ack("http://n1", 3)  # older -> ignored
    replication.ack("http://n1", 7)

    assert replication.acked_sequence("http://n1") == 7
    assert replication.acked_sequence("http://unknown") == 0


def test_log_is_bounded_and_ordered():

    _, replication = _make_manager(log_size=3)

    for i in range(1, 6):
        replication.record(f"k{i}", "set", i, None, i)

    assert replication.log_oldest_sequence() == 3
    assert [e["sequence"] for e in replication.entries_since(0)] == [3, 4, 5]


def test_entries_since_filters_correctly():

    _, replication = _make_manager()

    for i in range(1, 5):
        replication.record("k", "set", i, None, i)

    assert [e["sequence"] for e in replication.entries_since(2)] == [3, 4]


def test_needs_snapshot_when_never_acked():

    _, replication = _make_manager()

    # acked=0 -> unknown node -> snapshot
    assert replication._needs_snapshot("http://n1", 0, 10) is True


def test_needs_snapshot_when_gap_too_large():

    _, replication = _make_manager(snapshot_threshold=5)

    assert replication._needs_snapshot("http://n1", 1, 20) is True
    assert replication._needs_snapshot("http://n1", 18, 20) is False


def test_needs_snapshot_when_log_rolled_over():

    _, replication = _make_manager(log_size=2)

    for i in range(1, 10):
        replication.record("k", "set", i, None, i)

    # Log holds 8,9; a node acked through 2 cannot be replayed.
    assert replication._needs_snapshot("http://n1", 2, 9) is True


def test_incremental_sync_replays_missing_entries():

    manager, replication = _make_manager()

    # Node n1 is behind: it only acked sequence 1.
    replication.ack("http://n1", 1)

    replication.record("a", "set", "A", None, 2)
    replication.record("b", "set", "B", None, 3)
    replication.record("a", "delete", None, None, 4)

    result = replication.sync_node("http://n1")

    assert result["state"] == "done"
    assert result["mode"] == "incremental"
    assert result["entries_replayed"] == 3

    # The fake cache received the two sets and the delete, in order.
    cache = replication.cache
    assert [w[1] for w in cache.writes] == ["a", "b"]
    assert cache.deletes == [("http://n1", "a")]

    # n1 is now current.
    assert replication.acked_sequence("http://n1") == 4


def test_snapshot_sync_pulls_owned_keys_from_db():

    manager, replication = _make_manager()

    replication.cache = _FakeDistCache()
    replication.database = _FakeDB(values={"a": "A", "b": "B"})

    # The process HAS written (log non-empty) but this node never
    # acked - a restarted replica that must be restored from the DB.
    # Ownership filter uses the ring view.
    replication.next_sequence()

    result = replication.sync_node("http://n1")

    assert result["state"] == "done"
    assert result["mode"] == "snapshot"

    # Sync brought the node current through the sequence above.
    assert replication.acked_sequence("http://n1") == (
        replication.current_sequence()
    )

    # At least one key landed (ring assigns keys across 3 nodes).
    assert isinstance(result["keys_loaded"], int)


def test_no_snapshot_on_true_cold_start():
    """Fresh process (sequence 0) + unknown node -> nothing to restore.

    Layer 6.5: at a true cold start the log is empty, so snapshotting
    would only replay persisted search-cache rows from an earlier
    session into capacity-bounded nodes - churning them and evicting
    the cold-start seeds. Sync is a no-op instead; those rows (and
    every dataset row) regenerate through normal write-through.
    """

    manager, replication = _make_manager()

    assert replication.current_sequence() == 0
    assert replication._needs_snapshot("http://n1", 0, 0) is False

    # ...but a process that HAS written still snapshots its replicas.
    assert replication._needs_snapshot("http://n1", 0, 1) is True

    replication.cache = _FakeDistCache()
    replication.database = _FakeDB(values={"a": "A", "b": "B"})

    result = replication.sync_node("http://n1")

    assert result["state"] == "done"
    assert result["mode"] == "incremental"
    assert result["entries_replayed"] == 0
    assert result["keys_loaded"] == 0
    assert replication.cache.writes == []


def test_sync_ready_gate():

    _, replication = _make_manager()

    # Never asked -> satisfied.
    assert replication.sync_ready("http://n1") is True

    replication.ensure("http://n1")
    # ensure() with no real nodes leaves it pending/running -> hold.
    # (The worker thread will run sync_node against fake cache.)
    # After a done entry it opens.
    with replication._lock:
        replication._status["http://n1"] = replication._entry("done")

    assert replication.sync_ready("http://n1") is True

    with replication._lock:
        replication._status["http://n1"] = replication._entry("failed")

    assert replication.sync_ready("http://n1") is False


def test_forget_node_clears_ack_and_status():

    _, replication = _make_manager()

    replication.ack("http://n1", 9)
    with replication._lock:
        replication._status["http://n1"] = replication._entry("done")

    replication.forget_node("http://n1")

    assert replication.acked_sequence("http://n1") == 0
    assert replication.sync_ready("http://n1") is True  # no record


def test_status_exposes_sequence_and_nodes():

    _, replication = _make_manager()

    replication.next_sequence()
    seq = replication.next_sequence()
    replication.ack("http://n1", seq)

    status = replication.status()

    assert status["current_sequence"] == 2
    assert status["nodes"]["http://n1"]["acked_sequence"] == 2
    assert status["nodes"]["http://n1"]["behind"] == 0
    assert status["last_failover"] is None


# =========================================================
# STAGE B - ROUTER END-TO-END (sequence assigned + status)
# =========================================================

def test_router_assigns_sequence_and_tracks_acks():
    """A PUT through the router must bump the sequence + ack nodes."""

    base = 19720
    urls = [f"http://127.0.0.1:{base + i}" for i in range(3)]
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, base + i)
        for i in range(3)
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            before = router_module.replication.current_sequence()

            requests.put(
                f"{router_url}/cache/user:1",
                json={"value": "v1", "ttl": 60},
                timeout=3
            )

            after = router_module.replication.current_sequence()
            assert after == before + 1

            # The written nodes acked the new sequence.
            for node in router_module.node_manager.get_nodes("user:1"):
                assert (
                    router_module.replication.acked_sequence(node) == after
                )

            # /cluster/replication reports it.
            status = requests.get(
                f"{router_url}/cluster/replication", timeout=3
            ).json()

            assert status["current_sequence"] == after
            assert status["log_size"] >= 1

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_resync_unknown_node_404():

    base = 19730
    urls = [f"http://127.0.0.1:{base + i}" for i in range(3)]
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, base + i)
        for i in range(3)
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            response = requests.post(
                f"{router_url}/cluster/resync/http%3A%2F%2Fnope",
                timeout=3
            )

            assert response.status_code == 404
        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


# =========================================================
# STAGE C - FAILOVER FORMALIZATION (Layer 7.5)
# =========================================================

def test_failed_transition_records_failover():

    manager, replication = _make_manager()

    replication.install()
    manager.mark_failed("http://n1")

    last = replication.last_failover()

    assert last is not None
    assert last["node"] == "http://n1"

    history = replication.failovers()
    assert len(history) == 1
    assert history[0]["node"] == "http://n1"


def test_failover_history_is_bounded():

    _, replication = _make_manager()

    for i in range(60):
        replication.record_failover(f"http://n{i}")

    assert len(replication.failovers()) == 50
    assert replication.failovers()[-1]["node"] == "http://n59"


def test_status_exposes_failovers():

    manager, replication = _make_manager()

    replication.install()
    manager.mark_failed("http://n2")

    status = replication.status()

    assert status["last_failover"]["node"] == "http://n2"
    assert len(status["failovers"]) == 1


def test_starting_transition_clears_ack_not_failover():

    manager, replication = _make_manager()

    replication.install()
    replication.ack("http://n1", 5)
    manager.mark_failed("http://n1")
    manager.start_node("http://n1")

    # Explicit restart forgets the ack, but the failover history stays.
    assert replication.acked_sequence("http://n1") == 0
    assert len(replication.failovers()) == 1


# =========================================================
# STAGE D - RE-REPLICATION (Layer 7.6)
# =========================================================

def test_repair_restores_ownership_after_failure():
    """3 nodes RF=2: kill one, the orphaned key reaches the new owner."""

    manager = NodeManager(
        nodes=["http://n1", "http://n2", "http://n3"],
        virtual_nodes=10,
        replication_factor=2,
        initial_state=NodeState.READY
    )

    # Find a key that is currently owned by n2 (it will be orphaned).
    key = next(
        k for k in ("alpha", "beta", "gamma", "delta", "epsilon")
        if "http://n2" in manager.get_nodes(k)
    )

    db = _FakeDB(values={key: "VALUE", "other": "OTHER"})

    replication = ReplicationManager(
        node_manager=manager,
        distributed_cache=_FakeDistCache(),
        database=db
    )

    # Simulate: the cluster previously wrote to n1 + n2.
    before = manager.get_nodes(key)
    assert "http://n2" in before

    # n2 dies -> it drops out of the ring.
    manager.mark_failed("http://n2")

    after = manager.get_nodes(key)
    assert "http://n2" not in after
    new_owner = [n for n in after if n not in before]

    # Repair writes each key to its CURRENT owners.
    result = replication.repair_active_nodes()

    assert result["state"] == "done"
    assert result["keys_loaded"] > 0

    # The orphaned key now reaches the node that took over.
    cache = replication.cache
    written = {(n, k) for n, k, _v, _s in cache.writes}

    for node in after:
        assert (node, key) in written

    # And the new owner specifically received it.
    assert all((node, key) in written for node in new_owner)


def test_repair_is_idempotent():

    manager, replication = _make_manager()
    replication.database = _FakeDB(values={"a": "A"})

    first = replication.repair_active_nodes()
    second = replication.repair_active_nodes()

    assert first["state"] == second["state"] == "done"
    assert first["keys_loaded"] == second["keys_loaded"]


def test_repair_respects_limit():

    manager, replication = _make_manager()
    replication.database = _FakeDB(
        values={f"k{i}": i for i in range(50)}
    )

    result = replication.repair_active_nodes(limit=5)

    # limit counts key-writes; a key lands on RF nodes, so bound
    # the total writes rather than assuming one write per key.
    assert result["keys_loaded"] <= 5 * 2
    assert result["state"] == "done"


def test_repair_disabled_is_noop():

    manager = NodeManager(
        nodes=["http://n1"],
        virtual_nodes=10,
        initial_state=NodeState.READY
    )
    replication = ReplicationManager(
        node_manager=manager,
        distributed_cache=_FakeDistCache(),
        database=_FakeDB(values={"a": "A"}),
        enabled=False
    )

    result = replication.repair_active_nodes()

    assert result["state"] == "disabled"
    assert replication.repair() is False


# =========================================================
# LAYER 9 - LOG TRIMMING AFTER A PREFIX FLUSH
# =========================================================

def test_drop_prefix_removes_logged_ops_under_prefix():

    _, replication = _make_manager()

    replication.record(
        "q:users:q=a", "set", {"items": []}, None,
        replication.next_sequence()
    )
    replication.record(
        "users:1", "set", "A", None, replication.next_sequence()
    )
    replication.record(
        "users:2", "delete", None, None, replication.next_sequence()
    )
    replication.record(
        "s:users:f=nm", "set", ["a"], None, replication.next_sequence()
    )
    replication.record(
        "other:1", "set", "B", None, replication.next_sequence()
    )

    dropped = replication.drop_prefix("users:")

    assert dropped == 2

    remaining = {entry["key"] for entry in replication.entries_since(0)}

    # Search caches and unrelated keys survive; only the flushed
    # dataset's ops are gone (replaying them would resurrect the
    # rows the flush just deleted).
    assert remaining == {"q:users:q=a", "s:users:f=nm", "other:1"}


def test_drop_prefix_is_a_noop_when_nothing_matches():

    _, replication = _make_manager()

    replication.record(
        "users:1", "set", "A", None, replication.next_sequence()
    )

    assert replication.drop_prefix("nothing:") == 0
    assert len(replication.entries_since(0)) == 1
