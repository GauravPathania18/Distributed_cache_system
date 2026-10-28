"""
Lab 5 - live LRU visualization plumbing:

    node    GET  /stats/lru                 keys in recency order (MRU -> LRU)
    router  /cluster/nodes/{port}/...       single-node proxy, no hash-ring fan-out

Hermetic by construction:

  * the node under test is the real cache-node app on a high test
    port with a capacity-3 cache (rebuilt per test, restored after);
  * the router's pool is patched onto that port (+ one dead port for
    the 502 case), so the proxy can never reach a dev cluster;
  * the router starts with an empty cluster - no health monitoring,
    no warm-up, no seeded keys in the way of the lab keys.
"""

import threading
import time

import pytest
import requests
import uvicorn

from cache import CacheStatistics, LRUCache

import main as cache_node_module
import router.router as router_module
from router import routes_node


NODE_PORT = 19950
ROUTER_PORT = 19960
NODE_URL = f"http://127.0.0.1:{NODE_PORT}"
ROUTER_URL = f"http://127.0.0.1:{ROUTER_PORT}"
CAPACITY = 3


def _start_server(app, port):
    """Run a uvicorn server for an app in a background thread."""

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
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


def _put(url, key, value):
    response = requests.put(
        f"{url}/cache/{key}", json={"value": value}, timeout=3
    )
    assert response.status_code == 200
    return response.json()


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture(scope="module")
def servers():
    """Real node + real router, pool patched onto the test port."""

    original_base = routes_node.NODE_PORT_BASE
    original_max = routes_node.MAX_NODES

    # Pool = {19950 (live node), 19951 (deliberately dead)}.
    routes_node.NODE_PORT_BASE = NODE_PORT
    routes_node.MAX_NODES = 2

    router_module.configure_cluster([])

    node = _start_server(cache_node_module.app, NODE_PORT)
    router = _start_server(router_module.app, ROUTER_PORT)

    yield

    _stop_servers([router, node])
    routes_node.NODE_PORT_BASE = original_base
    routes_node.MAX_NODES = original_max
    router_module.configure_cluster([])


@pytest.fixture
def node_cache():
    """A fresh capacity-3 cache for one test (module objects restored)."""

    original_cache = cache_node_module.cache
    original_statistics = cache_node_module.statistics

    statistics = CacheStatistics(max_tracked_keys=100)
    cache_node_module.statistics = statistics
    cache_node_module.cache = LRUCache(
        capacity=CAPACITY,
        on_eviction=statistics.record_eviction
    )

    yield cache_node_module.cache

    cache_node_module.cache = original_cache
    cache_node_module.statistics = original_statistics


# =========================================================
# NODE: /stats/lru
# =========================================================

def test_node_lru_endpoint_reports_recency_order(servers, node_cache):
    """The list is served MRU -> LRU and every operation reorders it."""

    # Three inserts: each new key lands at HEAD.
    _put(NODE_URL, "a", 1)
    _put(NODE_URL, "b", 2)
    _put(NODE_URL, "c", 3)

    state = requests.get(f"{NODE_URL}/stats/lru", timeout=3).json()

    assert state["keys"] == ["c", "b", "a"]
    assert state["size"] == 3
    assert state["capacity"] == CAPACITY
    assert state["evictions"] == 0
    assert "hit_rate" in state

    # A read is a promotion: "a" jumps from tail to head...
    assert requests.get(f"{NODE_URL}/cache/a", timeout=3).status_code == 200

    state = requests.get(f"{NODE_URL}/stats/lru", timeout=3).json()
    assert state["keys"] == ["a", "c", "b"]

    # ...and a miss must not reorder anything.
    assert requests.get(f"{NODE_URL}/cache/missing", timeout=3).status_code == 404

    state = requests.get(f"{NODE_URL}/stats/lru", timeout=3).json()
    assert state["keys"] == ["a", "c", "b"]

    # Overflow: the tail ("b", demoted by the read of "a") falls off.
    _put(NODE_URL, "d", 4)

    state = requests.get(f"{NODE_URL}/stats/lru", timeout=3).json()

    assert state["keys"] == ["d", "a", "c"]
    assert state["size"] == CAPACITY        # never exceeds capacity
    assert state["evictions"] == 1
    assert state["hits"] == 1
    assert state["misses"] == 1


# =========================================================
# ROUTER: single-node proxy
# =========================================================

def test_router_proxies_every_cache_operation_to_one_node(servers, node_cache):

    # ---- state -------------------------------------------------
    state = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).json()

    assert state["keys"] == []
    assert state["capacity"] == CAPACITY

    # ---- writes land on THAT node only -------------------------
    for i in (1, 2, 3):
        response = requests.put(
            f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache/lab:lru:{i}",
            json={"value": {"i": i}},
            timeout=3,
        )
        assert response.status_code == 200

    state = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).json()

    assert state["keys"] == ["lab:lru:3", "lab:lru:2", "lab:lru:1"]
    assert state["size"] == 3

    # ---- touch: hit promotes, miss is a plain 404 ---------------
    hit = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache/lab:lru:1", timeout=3
    )
    assert hit.status_code == 200
    assert hit.json()["value"] == {"i": 1}

    miss = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache/nope", timeout=3
    )
    assert miss.status_code == 404

    state = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).json()
    assert state["keys"] == ["lab:lru:1", "lab:lru:3", "lab:lru:2"]

    # ---- overflow: the tail (lab:lru:2) is what evaporates ------
    requests.put(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache/lab:lru:4",
        json={"value": {"i": 4}},
        timeout=3,
    )

    state = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).json()

    assert state["evictions"] == 1
    assert state["keys"] == ["lab:lru:4", "lab:lru:1", "lab:lru:3"]

    # ---- explicit delete vs prefix flush -----------------------
    response = requests.delete(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache/lab:lru:1", timeout=3
    )
    assert response.status_code == 200

    state = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).json()
    assert state["keys"] == ["lab:lru:4", "lab:lru:3"]

    response = requests.delete(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-prefix/lab:lru:",
        timeout=3,
    )
    assert response.status_code == 200
    assert response.json()["removed"] == 2

    state = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).json()
    assert state["keys"] == []


# =========================================================
# ROUTER: guard rails
# =========================================================

def test_proxy_rejects_ports_outside_the_pool(servers, node_cache):

    response = requests.get(
        f"{ROUTER_URL}/cluster/nodes/1234/cache-state", timeout=3
    )

    assert response.status_code == 400
    assert "outside the node pool" in response.json()["detail"]


def test_proxy_reports_a_dead_pool_node_as_502(servers, node_cache):
    """Pool port NODE_PORT + 1 is in range but has no process behind it."""

    response = requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT + 1}/cache-state", timeout=5
    )

    assert response.status_code == 502
    assert "not reachable" in response.json()["detail"]

    # The live node next to it still answers - the failure is per port.
    assert requests.get(
        f"{ROUTER_URL}/cluster/nodes/{NODE_PORT}/cache-state", timeout=3
    ).status_code == 200
