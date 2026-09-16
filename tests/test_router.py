import threading
import time

import requests
import uvicorn

import main as cache_node_module
import router.router as router_module
from cluster.node_manager import NodeManager


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
    # Give threads time to release ports on Windows
    time.sleep(0.3)


def _make_urls(base_port):
    return [f"http://127.0.0.1:{base_port + i}" for i in range(3)]


def test_router_replicates_put_across_nodes():
    """PUT should write to primary + replica (2 nodes)."""

    base = 19100
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.node_manager = NodeManager(
            nodes=urls,
            virtual_nodes=100,
            replication_factor=2,
            health_interval=2,
            failure_threshold=3
        )

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            response = requests.put(
                f"{router_url}/cache/user:101",
                json={
                    "value": {"name": "Gaurav", "age": 21},
                    "ttl": 60
                },
                timeout=3
            )

            assert response.status_code == 200

            body = response.json()

            assert body["status"] == "stored"
            assert body["replicated_to"] == 2
            assert len(body["nodes"]) == 2

            # Both nodes should contain the key
            for node_url in body["nodes"]:

                direct = requests.get(
                    f"{node_url}/cache/user:101",
                    timeout=3
                )

                assert direct.status_code == 200
                assert direct.json()["value"]["name"] == "Gaurav"

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_get_falls_back_to_replica():
    """GET should try primary, then fall back to replica."""

    base = 19200
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.node_manager = NodeManager(
            nodes=urls,
            virtual_nodes=100,
            replication_factor=2,
            health_interval=2,
            failure_threshold=3
        )

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            # PUT first
            put_resp = requests.put(
                f"{router_url}/cache/user:202",
                json={"value": "hello", "ttl": 60},
                timeout=3
            )

            assert put_resp.status_code == 200

            stored_nodes = put_resp.json()["nodes"]

            # Kill the primary node
            primary_idx = urls.index(stored_nodes[0])
            node_servers[primary_idx].should_exit = True
            time.sleep(0.5)

            # GET should still succeed via replica
            get_resp = requests.get(
                f"{router_url}/cache/user:202",
                timeout=5
            )

            assert get_resp.status_code == 200
            assert get_resp.json()["data"]["value"] == "hello"

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_delete_invalidates_all_nodes():
    """DELETE should remove the key from both primary and replica."""

    base = 19300
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.node_manager = NodeManager(
            nodes=urls,
            virtual_nodes=100,
            replication_factor=2,
            health_interval=2,
            failure_threshold=3
        )

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            # PUT
            put_resp = requests.put(
                f"{router_url}/cache/user:303",
                json={"value": "delete-me", "ttl": 60},
                timeout=3
            )

            assert put_resp.status_code == 200
            stored_nodes = put_resp.json()["nodes"]

            # DELETE
            del_resp = requests.delete(
                f"{router_url}/cache/user:303",
                timeout=3
            )

            assert del_resp.status_code == 200

            # Key should be gone from ALL nodes that had it
            for node_url in stored_nodes:

                direct = requests.get(
                    f"{node_url}/cache/user:303",
                    timeout=3
                )

                assert direct.status_code == 404

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_cluster_status():
    """GET /cluster/status returns active/inactive node info."""

    base = 19400
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.node_manager = NodeManager(
            nodes=urls,
            virtual_nodes=100,
            replication_factor=2,
            health_interval=2,
            failure_threshold=3
        )

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            response = requests.get(
                f"{router_url}/cluster/status",
                timeout=3
            )

            assert response.status_code == 200

            body = response.json()

            assert body["total_nodes"] == 3
            assert len(body["active_nodes"]) == 3
            assert body["inactive_nodes"] == []
            assert body["replication_factor"] == 2

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_cache_miss():
    """GET for a non-existent key returns 404."""

    base = 19500
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.node_manager = NodeManager(
            nodes=urls,
            virtual_nodes=100,
            replication_factor=2,
            health_interval=2,
            failure_threshold=3
        )

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            response = requests.get(
                f"{router_url}/cache/does-not-exist",
                timeout=3
            )

            assert response.status_code == 404

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)
