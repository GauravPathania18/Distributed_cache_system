"""
Layer 9 - cluster control routes (scale up/down, start/stop/restart).

Safety rules that make these tests hermetic:

  * routes_manage.process_manager is replaced by a fake - no OS
    process is ever spawned, and stop() never touches a real PID;
  * routes_manage.NODE_PORT_BASE is monkeypatched high (18900) so
    even the HTTP shutdown attempt cannot reach a dev cluster on
    8001-8008;
  * check_node / _node_stats are served from the fake's own health
    set, so "the node booted" is instant - NODE_START_TIMEOUT never
    waits, and /cluster/nodes sees consistent process/stats data.

Each test starts from a freshly configured EMPTY cluster.
"""

import threading
import time

import pytest
import requests
import uvicorn

import router.router as router_module
from cluster.node_manager import NodeManager
from cluster.node_state import NodeState

from router import routes_manage


ROUTER_PORT = 18990
ROUTER_URL = f"http://127.0.0.1:{ROUTER_PORT}"
BASE = 18900            # fake node pool: 18900-18907
CAPACITY = 5000         # NODE_CAPACITY default, asserted below


def _start_server(app, port):
    """Run a uvicorn server for an app in a background thread."""

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error"
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
# FAKES
# =========================================================

class FakeProcessManager:
    """
    Stands in for services.process_manager: records spawn/stop calls
    and keeps a fake health set so patched check_node/_node_stats
    answer instantly.
    """

    def __init__(self):
        self.running = set()        # ports
        self.health = set()         # urls that answer HTTP
        self.started = []           # (port, capacity)
        self.stopped = []           # (port, force)
        self._pids = {}

    @staticmethod
    def _url(port):
        return f"http://127.0.0.1:{port}"

    def start(self, port, capacity=5000):

        if port in self.running:
            return {
                "port": port, "started": False, "already_running": True,
                "pid": self._pids[port], "log": f"node-{port}.log",
            }

        self.running.add(port)
        self.started.append((port, capacity))
        self._pids[port] = 50000 + port
        self.health.add(self._url(port))

        return {
            "port": port, "started": True, "already_running": False,
            "pid": self._pids[port], "log": f"node-{port}.log",
        }

    def stop(self, port, grace=3.0, force=True):

        self.stopped.append((port, force))
        self.health.discard(self._url(port))

        if port in self.running:
            self.running.discard(port)
            self._pids.pop(port, None)
            return {"port": port, "stopped": True, "method": "child"}

        return {"port": port, "stopped": False, "reason": "not-running"}

    def is_running(self, port):
        return port in self.running

    def pid(self, port):
        return self._pids.get(port)

    def stop_all(self):
        return len(self.running)


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture(scope="module")
def server():
    srv = _start_server(router_module.app, ROUTER_PORT)
    yield srv
    _stop_servers([srv])


@pytest.fixture
def fake(monkeypatch):

    fm = FakeProcessManager()

    monkeypatch.setattr(routes_manage, "process_manager", fm)
    monkeypatch.setattr(routes_manage, "NODE_PORT_BASE", BASE)
    monkeypatch.setattr(
        routes_manage, "check_node",
        lambda url: url in fm.health
    )
    monkeypatch.setattr(
        routes_manage, "_node_stats",
        lambda url: {
            "size": 0, "capacity": CAPACITY,
            "hits": 0, "misses": 0, "evictions": 0,
        } if url in fm.health else None
    )

    router_module.configure_cluster([])

    yield fm

    # Never leave a cluster behind for the next module.
    router_module.configure_cluster([])


# =========================================================
# SCALE ENDPOINT
# =========================================================

def test_scale_up_spawns_and_registers_nodes(server, fake):

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 3}, timeout=10
    )

    assert response.status_code == 200

    body = response.json()

    assert body["target"] == 3
    assert body["total"] == 3
    assert len(body["added"]) == 3
    assert body["failed"] == []
    assert all(record["healthy"] for record in body["added"])
    assert [record["port"] for record in body["added"]] == [
        BASE, BASE + 1, BASE + 2
    ]

    # Real spawns went through the (fake) process manager.
    assert fake.started == [
        (BASE, CAPACITY), (BASE + 1, CAPACITY), (BASE + 2, CAPACITY)
    ]

    # The listing shows them: owned processes, gated in STARTING.
    listing = requests.get(f"{ROUTER_URL}/cluster/nodes", timeout=5).json()

    assert listing["total"] == 3
    assert [node["port"] for node in listing["nodes"]] == [
        BASE, BASE + 1, BASE + 2
    ]

    for node in listing["nodes"]:
        assert node["state"] == "starting"   # health monitor gates it
        assert node["process"] == "owned"
        assert node["pid"] is not None
        assert node["stats"]["capacity"] == CAPACITY

    assert listing["max_nodes"] >= 3
    assert listing["node_port_base"] == BASE
    assert len(listing["pool"]) == listing["max_nodes"]


def test_scale_down_stops_highest_ports_first(server, fake):

    requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 3}, timeout=10
    )

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 1}, timeout=10
    )

    body = response.json()

    assert body["total"] == 1
    assert len(body["removed"]) == 2

    # Highest ports leave first...
    assert [record["port"] for record in body["removed"]] == [
        BASE + 2, BASE + 1
    ]
    assert all(
        record["removed_from_cluster"] for record in body["removed"]
    )
    assert all(record["process"]["stopped"] for record in body["removed"])

    # ...and they are really gone: no spawn-records, no ring entries.
    assert fake.running == {BASE}

    listing = requests.get(f"{ROUTER_URL}/cluster/nodes", timeout=5).json()

    assert [node["port"] for node in listing["nodes"]] == [BASE]

    manager = router_module.node_manager

    assert manager.nodes == {f"http://127.0.0.1:{BASE}"}

    for key in ("alpha", "beta", "gamma", "delta", "epsilon"):
        assert f"http://127.0.0.1:{BASE + 2}" not in manager.get_nodes(key)


def test_scale_to_same_size_is_a_noop(server, fake):

    requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 2}, timeout=10
    )

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 2}, timeout=10
    )

    body = response.json()

    assert body["added"] == []
    assert body["removed"] == []
    assert body["failed"] == []
    assert body["total"] == 2
    assert len(fake.started) == 2   # nothing new spawned


def test_scale_rejects_target_beyond_pool(server, fake):

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 99}, timeout=5
    )

    assert response.status_code == 422   # Field(le=MAX_NODES)
    assert fake.started == []


def test_scale_down_to_zero_empties_the_cluster(server, fake):

    requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 2}, timeout=10
    )

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes", json={"target": 0}, timeout=10
    )

    body = response.json()

    assert body["total"] == 0
    assert len(body["removed"]) == 2
    assert fake.running == set()

    listing = requests.get(f"{ROUTER_URL}/cluster/nodes", timeout=5).json()

    assert listing["nodes"] == []
    assert router_module.node_manager.nodes == set()


# =========================================================
# SINGLE-NODE ENDPOINTS
# =========================================================

def test_start_requires_a_pool_port(server, fake):

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/1234/start", timeout=5
    )

    assert response.status_code == 400
    assert fake.started == []


def test_start_adopts_a_node_that_already_answers(server, fake):

    # Something is already serving on the pool port (e.g. run.py or a
    # hand-started node): the router must adopt it, never spawn a
    # second one.
    url = f"http://127.0.0.1:{BASE + 6}"
    fake.health.add(url)

    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/{BASE + 6}/start", timeout=10
    )

    body = response.json()

    assert response.status_code == 200
    assert body["action"] == "attached"
    assert body["healthy"] is True
    assert fake.started == []           # no spawn happened
    assert url in router_module.node_manager.nodes


def test_start_stop_restart_cycle(server, fake):

    # ---- start ------------------------------------------------
    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/{BASE + 5}/start", timeout=10
    )

    body = response.json()

    assert response.status_code == 200
    assert body["action"] == "started"
    assert body["healthy"] is True
    assert body["state"] == "starting"
    assert fake.started == [(BASE + 5, CAPACITY)]

    # Starting again: the live node is adopted, not respawned.
    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/{BASE + 5}/start", timeout=10
    )

    assert response.json()["action"] == "attached"
    assert len(fake.started) == 1

    # ---- stop -------------------------------------------------
    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/{BASE + 5}/stop", timeout=10
    )

    body = response.json()

    assert body["removed_from_cluster"] is True
    assert body["process"]["stopped"] is True
    assert body["state"] is None            # no longer a member
    assert BASE + 5 not in fake.running

    listing = requests.get(f"{ROUTER_URL}/cluster/nodes", timeout=5).json()

    assert all(node["port"] != BASE + 5 for node in listing["nodes"])

    # Stopping an already-stopped port is honest, not an error.
    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/{BASE + 5}/stop", timeout=10
    )

    body = response.json()

    assert body["removed_from_cluster"] is False
    assert body["process"]["stopped"] is False
    assert body["process"]["reason"] == "not-running"

    # ---- restart ----------------------------------------------
    response = requests.post(
        f"{ROUTER_URL}/cluster/nodes/{BASE + 5}/restart", timeout=10
    )

    body = response.json()

    assert response.status_code == 200
    assert body["action"] == "started"
    assert body["healthy"] is True
    assert body["state"] == "starting"      # re-enters through STARTING
    assert BASE + 5 in fake.running
    assert len(fake.started) == 2           # initial + restart


# =========================================================
# RING BOOKKEEPING (unit level)
# =========================================================

def test_deregister_node_drops_it_from_ring_and_active_set():

    manager = NodeManager(
        nodes=["http://n1:8001", "http://n2:8002"],
        virtual_nodes=10,
        initial_state=NodeState.READY
    )

    # With RF=2 and two READY nodes, both own every key.
    assert all(
        "http://n1:8001" in manager.get_nodes(key)
        for key in ("alpha", "beta", "gamma")
    )

    assert manager.deregister_node("http://n1:8001") is True

    # Known side bookkeeping targets...
    assert manager.nodes == {"http://n2:8002"}
    assert "http://n1:8001" not in manager.active_nodes
    assert "http://n1:8001" not in manager.node_states
    assert "http://n1:8001" not in manager.failure_counts

    # ...and no key can be routed to it anymore.
    for key in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
        assert "http://n1:8001" not in manager.get_nodes(key)

    # Removing an unknown node reports False instead of raising.
    assert manager.deregister_node("http://n1:8001") is False
