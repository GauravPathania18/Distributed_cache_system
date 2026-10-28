import threading
import time

import pytest
import requests
import uvicorn

from cache import CacheStatistics, LRUCache

import main as cache_node_module


# =========================================================
# COUNTERS
# =========================================================

def test_record_events_counts_operations():
    stats = CacheStatistics()

    stats.record_hit("user:42")
    stats.record_hit("user:42")
    stats.record_hit("user:91")
    stats.record_miss("user:17")
    stats.record_set()
    stats.record_set()
    stats.record_delete()
    stats.record_eviction()

    snapshot = stats.snapshot()

    assert snapshot["hits"] == 3
    assert snapshot["misses"] == 1
    assert snapshot["total_gets"] == 4
    assert snapshot["sets"] == 2
    assert snapshot["deletes"] == 1
    assert snapshot["evictions"] == 1
    assert snapshot["hit_rate"] == pytest.approx(0.75)


def test_hit_rate_is_zero_without_gets():
    stats = CacheStatistics()

    stats.record_set()

    snapshot = stats.snapshot()

    assert snapshot["total_gets"] == 0
    assert snapshot["hit_rate"] == 0.0


def test_misses_still_count_as_key_access():
    """Warm-up cares that a key was requested, hit or not."""

    stats = CacheStatistics()

    stats.record_miss("user:42")
    stats.record_miss("user:42")
    stats.record_hit("user:42")

    assert stats.top_keys(1) == [("user:42", 3)]


def test_snapshot_does_not_include_raw_keys_by_default():
    stats = CacheStatistics()

    stats.record_hit("user:42")

    assert "key_access" not in stats.snapshot()
    assert stats.snapshot(include_keys=True)["key_access"] == {
        "user:42": 1
    }


# =========================================================
# HOT KEYS
# =========================================================

def test_top_keys_ranking():
    stats = CacheStatistics()

    for key, count in [
        ("user:42", 5),
        ("product:10", 3),
        ("user:91", 4),
    ]:
        for _ in range(count):
            stats.record_hit(key)

    assert stats.top_keys(2) == [
        ("user:42", 5),
        ("user:91", 4),
    ]

    assert stats.top_keys() == [
        ("user:42", 5),
        ("user:91", 4),
        ("product:10", 3),
    ]


# =========================================================
# BOUNDED KEY TRACKING
# =========================================================

def test_key_tracking_is_bounded():
    stats = CacheStatistics(max_tracked_keys=3)

    for key in ("a", "b", "c"):
        stats.record_hit(key)

    # The bound is reached.
    assert stats.snapshot()["tracked_keys"] == 3

    # A brand-new key is not tracked...
    stats.record_hit("d")
    stats.record_hit("e")

    snapshot = stats.snapshot()
    assert snapshot["tracked_keys"] == 3
    assert snapshot["untracked"] == 2

    # ...but global counters still count every event...
    assert snapshot["hits"] == 5

    # ...and known keys keep accumulating.
    stats.record_hit("a")

    assert stats.top_keys(1) == [("a", 2)]
    assert stats.snapshot()["untracked"] == 2


def test_invalid_constructor_arguments():
    with pytest.raises(ValueError):
        CacheStatistics(max_tracked_keys=0)

    with pytest.raises(ValueError):
        CacheStatistics(window_seconds=0)


# =========================================================
# WINDOW ROTATION
# =========================================================

def test_rotate_resets_window_and_keeps_last():
    stats = CacheStatistics()

    stats.record_hit("user:42")
    stats.record_hit("user:42")
    stats.record_miss("user:91")
    stats.record_set()

    closed = stats.rotate()

    assert closed["hits"] == 2
    assert closed["misses"] == 1
    assert closed["sets"] == 1
    assert closed["hit_rate"] == pytest.approx(2 / 3)
    assert closed["top_keys"] == [("user:42", 2), ("user:91", 1)]
    assert closed["window_ended_at"] >= closed["window_started_at"]

    # The new window starts from zero.
    snapshot = stats.snapshot()
    assert snapshot["hits"] == 0
    assert snapshot["total_gets"] == 0
    assert snapshot["tracked_keys"] == 0
    assert snapshot["last_window"] == closed

    # And the same key starts counting from scratch.
    stats.record_hit("user:42")
    assert stats.top_keys(1) == [("user:42", 1)]


def test_automatic_rotation_thread():
    stats = CacheStatistics(window_seconds=0.05, auto_rotate=True)

    try:
        stats.record_hit("user:42")

        deadline = time.time() + 2.0
        while stats.last_window is None and time.time() < deadline:
            time.sleep(0.01)

        assert stats.last_window is not None
        assert stats.last_window["hits"] == 1
        assert stats.last_window["top_keys"] == [("user:42", 1)]

        # The current window was reset by the rotation.
        assert stats.snapshot()["hits"] == 0
    finally:
        stats.stop()

    assert stats._thread is None


def test_start_is_idempotent_and_stop_terminates():
    stats = CacheStatistics(window_seconds=300, auto_rotate=True)

    thread = stats._thread
    assert thread is not None and thread.is_alive()

    stats.start()   # no second thread
    assert stats._thread is thread

    stats.stop()
    assert stats._thread is None
    assert not thread.is_alive()

    # Manual rotation still works after stop().
    stats.record_set()
    assert stats.rotate()["sets"] == 1


def test_rotation_thread_is_daemon():
    stats = CacheStatistics(window_seconds=300, auto_rotate=True)
    try:
        assert stats._thread.daemon
    finally:
        stats.stop()


def test_concurrent_recording_is_consistent():
    stats = CacheStatistics()

    def hammer():
        for _ in range(250):
            stats.record_hit("user:42")
            stats.record_miss("user:91")
            stats.record_set()

    threads = [threading.Thread(target=hammer) for _ in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    snapshot = stats.snapshot()

    assert snapshot["hits"] == 1000
    assert snapshot["misses"] == 1000
    assert snapshot["sets"] == 1000
    assert snapshot["tracked_keys"] == 2
    assert stats.top_keys(2) == [
        ("user:42", 1000),
        ("user:91", 1000),
    ]


# =========================================================
# EVICTION HOOK (LRUCache -> CacheStatistics)
# =========================================================

def test_lru_eviction_calls_hook_with_key():
    evicted = []

    cache = LRUCache(capacity=2, on_eviction=evicted.append)

    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)       # evicts "a"

    assert evicted == ["a"]
    assert cache.evictions == 1
    assert not cache.exists("a")


def test_lru_eviction_hook_is_optional():
    cache = LRUCache(capacity=1)

    cache.set("a", 1)
    cache.set("b", 2)       # evicts "a", no hook configured

    assert cache.evictions == 1
    assert not cache.exists("a")


# =========================================================
# NODE SERVER ENDPOINTS (Layer 6.2)
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


def test_node_statistics_endpoints():
    """The node records operations and exposes them through HTTP."""

    port = 19900
    url = f"http://127.0.0.1:{port}"

    # Isolate this test from the module-level objects the other
    # tests share (restore them afterwards).
    original_cache = cache_node_module.cache
    original_statistics = cache_node_module.statistics

    cache_node_module.statistics = CacheStatistics(max_tracked_keys=100)
    cache_node_module.cache = LRUCache(
        capacity=2,
        on_eviction=cache_node_module.statistics.record_eviction
    )

    server = _start_server(cache_node_module.app, port)

    try:

        # 4 sets, 2 hits, 1 miss, 1 delete, 1 capacity eviction.
        assert requests.put(
            f"{url}/cache/user:42",
            json={"value": "A", "ttl": 60},
            timeout=3
        ).status_code == 200

        assert requests.put(
            f"{url}/cache/user:91",
            json={"value": "B", "ttl": 60},
            timeout=3
        ).status_code == 200

        assert requests.get(f"{url}/cache/user:42", timeout=3).status_code == 200
        assert requests.get(f"{url}/cache/user:42", timeout=3).status_code == 200
        assert requests.get(f"{url}/cache/missing:1", timeout=3).status_code == 404

        assert requests.delete(f"{url}/cache/user:91", timeout=3).status_code == 200

        # Fill the capacity-2 cache again, then overflow it.
        assert requests.put(
            f"{url}/cache/product:10",
            json={"value": "C", "ttl": 60},
            timeout=3
        ).status_code == 200

        assert requests.put(
            f"{url}/cache/product:11",
            json={"value": "D", "ttl": 60},
            timeout=3
        ).status_code == 200

        window = requests.get(f"{url}/stats/window?limit=5", timeout=3).json()

        assert window["hits"] == 2
        assert window["misses"] == 1
        assert window["total_gets"] == 3
        assert window["sets"] == 4
        assert window["deletes"] == 1
        assert window["evictions"] == 1
        assert window["hit_rate"] == pytest.approx(2 / 3)
        assert window["tracked_keys"] == 2
        assert window["last_window"] is None

        top_keys = dict(
            (key, count) for key, count in window["top_keys"]
        )
        assert top_keys == {"user:42": 2, "missing:1": 1}

        ranking = requests.get(
            f"{url}/stats/top-keys?limit=1",
            timeout=3
        ).json()

        assert ranking["top_keys"] == [["user:42", 2]]
        assert ranking["tracked_keys"] == 2
        assert ranking["window_seconds"] == 300

        # The plain Layer 1 stats endpoint still works unchanged.
        legacy = requests.get(f"{url}/stats", timeout=3).json()

        assert legacy["capacity"] == 2
        assert legacy["size"] == 2
        assert legacy["evictions"] == 1
        assert "hit_rate" in legacy

    finally:
        server.should_exit = True
        time.sleep(0.3)
        cache_node_module.cache = original_cache
        cache_node_module.statistics = original_statistics
