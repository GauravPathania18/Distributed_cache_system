import threading
import time

import pytest
import requests
import uvicorn

from database.exceptions import DatabaseUnavailableError

import main as cache_node_module
import router.router as router_module


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


# =========================================================
# LAYER 4 TESTS (replication / failure detection)
# =========================================================

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
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

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
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

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

            body = get_resp.json()

            assert body["value"] == "hello"
            assert body["source"] == "cache"
            assert body["node"] == stored_nodes[1]

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_cache_delete_evicts_only():
    """DELETE /cache evicts from every node but keeps the DB row."""

    base = 19300
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

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

            # DELETE (evict-only half of the deletion split)
            del_resp = requests.delete(
                f"{router_url}/cache/user:303",
                timeout=3
            )

            assert del_resp.status_code == 200
            assert del_resp.json()["status"] == "evicted"
            assert del_resp.json()["database"] == "kept"

            # Key should be gone from ALL nodes that had it
            for node_url in stored_nodes:

                direct = requests.get(
                    f"{node_url}/cache/user:303",
                    timeout=3
                )

                assert direct.status_code == 404

            # ...but the database row MUST survive an eviction
            db_resp = requests.get(
                f"{router_url}/db/user:303",
                timeout=3
            )

            assert db_resp.status_code == 200
            assert db_resp.json()["value"] == "delete-me"

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_db_delete_cascades_to_nodes():
    """DELETE /db removes the row AND evicts every node copy."""

    base = 19900
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            put_resp = requests.put(
                f"{router_url}/cache/user:304",
                json={"value": "cascade-me", "ttl": 60},
                timeout=3
            )

            assert put_resp.status_code == 200
            stored_nodes = put_resp.json()["nodes"]
            assert stored_nodes

            del_resp = requests.delete(
                f"{router_url}/db/user:304",
                timeout=3
            )

            assert del_resp.status_code == 200
            assert del_resp.json()["status"] == "deleted"
            assert set(del_resp.json()["nodes"]) == set(stored_nodes)

            # Row gone...
            assert requests.get(
                f"{router_url}/db/user:304", timeout=3
            ).status_code == 404

            # ...and every node copy with it
            for node_url in stored_nodes:
                assert requests.get(
                    f"{node_url}/cache/user:304", timeout=3
                ).status_code == 404

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
        router_module.configure_cluster(urls, replication_factor=2)

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

            # Layer 6.1 - lifecycle states
            assert body["node_states"] == {
                urls[0]: "ready",
                urls[1]: "ready",
                urls[2]: "ready",
            }
            assert body["recovering_nodes"] == []
            assert body["failed_nodes"] == []

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_router_cache_miss():
    """GET for a key missing from both cache and database returns 404."""

    base = 19500
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

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


# =========================================================
# LAYER 5 TESTS (cache-aside / database fallback)
# =========================================================

def test_cache_aside_populates_from_database():
    """A cache miss should be served from the database and re-cached."""

    base = 19600
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            # PUT -> database + cache
            put_resp = requests.put(
                f"{router_url}/cache/user:500",
                json={"value": {"name": "Alex"}, "ttl": 60},
                timeout=3
            )

            assert put_resp.status_code == 200

            # First GET -> cache hit
            first = requests.get(
                f"{router_url}/cache/user:500",
                timeout=3
            )

            assert first.status_code == 200
            assert first.json()["source"] == "cache"

            # Evict the key from every cache node (database keeps it)
            for node_url in urls:

                requests.delete(
                    f"{node_url}/cache/user:500",
                    timeout=3
                )

            # GET -> cache miss, served from database
            second = requests.get(
                f"{router_url}/cache/user:500",
                timeout=3
            )

            assert second.status_code == 200

            body = second.json()

            assert body["source"] == "database"
            assert body["value"]["name"] == "Alex"
            assert body["node"] is None

            # GET again -> back in the cache
            third = requests.get(
                f"{router_url}/cache/user:500",
                timeout=3
            )

            assert third.status_code == 200
            assert third.json()["source"] == "cache"

            router_module.database.delete("user:500")

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_db_direct_endpoints():
    """The temporary /db endpoints read and delete straight from SQLite."""

    base = 19700
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            put_resp = requests.put(
                f"{router_url}/cache/user:501",
                json={"value": "db-direct", "ttl": 60},
                timeout=3
            )

            assert put_resp.status_code == 200

            get_resp = requests.get(
                f"{router_url}/db/user:501",
                timeout=3
            )

            assert get_resp.status_code == 200
            assert get_resp.json()["value"] == "db-direct"
            assert get_resp.json()["source"] == "database"

            del_resp = requests.delete(
                f"{router_url}/db/user:501",
                timeout=3
            )

            assert del_resp.status_code == 200

            missing = requests.get(
                f"{router_url}/db/user:501",
                timeout=3
            )

            assert missing.status_code == 404

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


# =========================================================
# LAYER 5.7 TESTS (database failures / circuit breaker)
# =========================================================

def test_router_503_when_database_down_and_breaker_status(monkeypatch):
    """DB failure -> 503; cache hits still served; breaker trips to open."""

    base = 19800
    ports = [base, base + 1, base + 2]
    urls = _make_urls(base)
    router_port = base + 10

    node_servers = [
        _start_server(cache_node_module.app, port)
        for port in ports
    ]

    def broken_get(key):
        raise DatabaseUnavailableError("simulated outage")

    monkeypatch.setattr(router_module.database, "get", broken_get)

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            cfg = router_module.cache_service.circuit_breaker
            assert cfg.get_state() == "closed"

            # 1. Prime the cache while the DB is healthy (get still broken
            #    from here on, but set/delete are untouched).
            put_resp = requests.put(
                f"{router_url}/cache/user:600",
                json={"value": "cached-while-up", "ttl": 60},
                timeout=3
            )

            assert put_resp.status_code == 200

            # 2. A cache hit must still be served even with the DB down.
            hit = requests.get(
                f"{router_url}/cache/user:600",
                timeout=3
            )

            assert hit.status_code == 200
            assert hit.json()["source"] == "cache"
            assert hit.json()["value"] == "cached-while-up"

            # 3. A cache miss with the DB down -> 503 (not 500).
            first = requests.get(
                f"{router_url}/cache/missing:1",
                timeout=3
            )

            assert first.status_code == 503

            # 4. A second miss -> still 503 (failure 2).
            second = requests.get(
                f"{router_url}/cache/missing:1",
                timeout=3
            )

            assert second.status_code == 503

            # 5. Third miss trips the breaker (failure_threshold=3).
            third = requests.get(
                f"{router_url}/cache/missing:2",
                timeout=3
            )

            assert third.status_code == 503

            assert cfg.get_state() == "open"

            # 6. Status endpoint reflects the open state.
            status = requests.get(
                f"{router_url}/circuit-breaker/status",
                timeout=3
            )

            assert status.status_code == 200
            assert status.json()["state"] == "open"

            # 7. While open, subsequent misses are rejected fast (503).
            while_open = requests.get(
                f"{router_url}/cache/missing:3",
                timeout=3
            )

            assert while_open.status_code == 503

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)