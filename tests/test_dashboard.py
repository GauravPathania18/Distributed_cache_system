import threading
import time

import requests
import uvicorn

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
    time.sleep(0.3)


# =========================================================
# DASHBOARD AGGREGATE
# =========================================================

def test_dashboard_aggregate_shape_and_node_error_tolerance():
    """Aggregate must return every panel even when nodes are down."""

    base = 19810
    urls = [f"http://127.0.0.1:{base + i}" for i in range(3)]
    router_port = base + 10

    router_module.configure_cluster(urls, replication_factor=2)

    router_server = _start_server(router_module.app, router_port)
    router_url = f"http://127.0.0.1:{router_port}"

    try:
        response = requests.get(
            f"{router_url}/dashboard/aggregate", timeout=10
        )

        assert response.status_code == 200
        data = response.json()

        for panel in (
            "telemetry", "aggregate", "nodes", "cluster",
            "circuit_breaker", "hot_keys", "top_keys",
            "warmup", "replication",
        ):
            assert panel in data

        # Nodes are unreachable here -> each reports an inline error
        # instead of failing the whole endpoint.
        assert len(data["nodes"]) == 3
        for snapshot in data["nodes"].values():
            assert "error" in snapshot
            assert "lifetime" in snapshot

        # Hit-rate fields are normalized to percentages (0-100).
        assert 0.0 <= data["aggregate"]["hit_rate_pct"] <= 100.0
        assert 0.0 <= data["aggregate"]["window_hit_rate_pct"] <= 100.0

    finally:
        _stop_servers([router_server])


def test_dashboard_telemetry_reflects_traffic_and_resets():
    """Telemetry must count real operations and reset on demand."""

    base = 19820
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
            requests.post(
                f"{router_url}/dashboard/telemetry/reset", timeout=5
            )

            # One write + repeated reads of the same key.
            requests.put(
                f"{router_url}/cache/user:99",
                json={"value": "v", "ttl": 60},
                timeout=5
            )

            for _ in range(3):
                requests.get(f"{router_url}/cache/user:99", timeout=5)

            telemetry = requests.get(
                f"{router_url}/dashboard/aggregate", timeout=10
            ).json()["telemetry"]

            assert telemetry["sets"] == 1
            # First read back-fills from the DB; the rest are cache hits.
            assert telemetry["gets_cache"] >= 2
            assert telemetry["db_reads_avoided"] >= 2

            # Reset zeroes the counters.
            requests.post(
                f"{router_url}/dashboard/telemetry/reset", timeout=5
            )

            after = requests.get(
                f"{router_url}/dashboard/aggregate", timeout=10
            ).json()["telemetry"]

            assert after["gets_cache"] == 0
            assert after["sets"] == 0

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)


def test_search_endpoint_caches_result_set_end_to_end():
    """First search runs SQL; the repeat is a cache hit from a node."""

    base = 19830
    urls = [f"http://127.0.0.1:{base + i}" for i in range(3)]
    router_port = base + 10

    # Entities are generated from the dataset registry now, so the
    # test registers the dataset it expects to search. Prefix stays
    # "product:" (legacy) while the dataset name is "products".
    registry = router_module.database.registry

    registry.register(
        "products",
        prefix="product:",
        file_format="csv",
        key_column="id",
        row_count=2,
        schema={
            "name": {"type": "string", "distinct": 2,
                     "values": ["Eta widget", "Zeta widget"]},
            "price": {"type": "number", "distinct": 2, "values": None},
            "category": {"type": "string", "distinct": 1,
                         "values": ["tools"]},
            "stock": {"type": "number", "distinct": 2, "values": None},
            "rating": {"type": "number", "distinct": 2, "values": None},
        },
    )

    node_servers = [
        _start_server(cache_node_module.app, base + i)
        for i in range(3)
    ]

    try:
        router_module.configure_cluster(urls, replication_factor=2)
        router_module.database.clear()
        # Product keys route into the dataset's own file; q:/s: search
        # cache keys still land in the system store.
        router_module.database.set_many([
            ("product:t-1", {"name": "Zeta widget", "price": 5.0,
                             "category": "tools", "stock": 3, "rating": 4.2}),
            ("product:t-2", {"name": "Eta widget", "price": 8.0,
                             "category": "tools", "stock": 1, "rating": 3.9}),
        ])

        router_server = _start_server(router_module.app, router_port)
        router_url = f"http://127.0.0.1:{router_port}"

        try:
            query = "/search/products?q=widget&sort=price&dir=asc"

            first = requests.get(f"{router_url}{query}", timeout=10).json()
            assert first["source"] == "database"
            assert first["total"] == 2
            assert first["cache_key"].startswith("q:products:")

            second = requests.get(f"{router_url}{query}", timeout=10).json()
            assert second["source"] == "cache"
            assert second["total"] == 2

            # Unknown entity is a 422, not a crash.
            bad = requests.get(f"{router_url}/search/nope", timeout=5)
            assert bad.status_code == 422

            # Suggest + browse work over HTTP.
            s = requests.get(
                f"{router_url}/search/products/suggest?field=category",
                timeout=5
            )
            assert s.status_code == 200
            assert "tools" in s.json()["values"]

            b = requests.get(
                f"{router_url}/browse/prefix?prefix=product:&limit=10",
                timeout=5
            )
            assert b.status_code == 200
            assert len(b.json()["keys"]) == 2

        finally:
            _stop_servers([router_server])
    finally:
        _stop_servers(node_servers)
        registry.unregister("products")


# =========================================================
# STATIC UI
# =========================================================

def test_root_redirects_to_ui_and_ui_serves_html():
    """The web UI must be reachable at / and served at /ui."""

    base = 19840
    urls = [f"http://127.0.0.1:{base + i}" for i in range(3)]
    router_port = base + 10

    router_module.configure_cluster(urls, replication_factor=2)

    router_server = _start_server(router_module.app, router_port)
    router_url = f"http://127.0.0.1:{router_port}"

    try:
        root = requests.get(
            f"{router_url}/", allow_redirects=True, timeout=5
        )
        assert root.status_code == 200
        assert "<title>" in root.text

        page = requests.get(f"{router_url}/ui", timeout=5)
        assert page.status_code == 200
        assert "DCS" in page.text

        css = requests.get(f"{router_url}/ui/style.css", timeout=5)
        assert css.status_code == 200

        js = requests.get(f"{router_url}/ui/app.js", timeout=5)
        assert js.status_code == 200

    finally:
        _stop_servers([router_server])
