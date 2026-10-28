"""
Layer 9 - dataset management API (create / browse / add / delete).

Hermetic by construction:

  * the router runs with an EMPTY cluster (configure_cluster([])),
    so every fan-out is a no-op and mutations touch only the
    database side of the consistency chain;
  * the module-scoped `node` fixture is a REAL cache node used by
    exactly the tests that need one (prefix flush, search-cache
    purge on a live node);
  * the `base` fixture removes every dataset after each test, so
    the shared session registry stays clean for later modules.
"""

import os
import threading
import time
from urllib.parse import quote

import pytest
import requests
import uvicorn

import main as cache_node_module
import router.router as router_module

from cluster.node_manager import NodeManager
from cluster.node_state import NodeState
from services.distributed_cache import DistributedCache


ROUTER_PORT = 18960
ROUTER_URL = f"http://127.0.0.1:{ROUTER_PORT}"
NODE_PORT = 18971


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
# FIXTURES
# =========================================================

@pytest.fixture(scope="module")
def server():
    srv = _start_server(router_module.app, ROUTER_PORT)
    yield srv
    _stop_servers([srv])


@pytest.fixture(scope="module")
def node():
    """One real cache node - only the tests that need it request it."""
    srv = _start_server(cache_node_module.app, NODE_PORT)
    yield f"http://127.0.0.1:{NODE_PORT}"
    _stop_servers([srv])


@pytest.fixture
def base(server):
    """Empty cluster + guaranteed dataset cleanup after the test."""

    router_module.configure_cluster([])

    yield ROUTER_URL

    registry = router_module.database.registry

    for name in list(registry.entries()):
        try:
            requests.delete(f"{ROUTER_URL}/datasets/{name}", timeout=5)
        except requests.RequestException:
            registry.unregister(name)


# =========================================================
# DATASET LIFECYCLE
# =========================================================

def test_create_list_get_delete_dataset(base):

    # ---- create (the name is slugged, the file materializes now)
    response = requests.post(
        f"{ROUTER_URL}/datasets",
        json={"name": "Widgets Inc"},
        timeout=5
    )

    assert response.status_code == 200

    body = response.json()
    dataset = body["dataset"]

    assert body["status"] == "created"
    assert dataset["name"] == "widgets_inc"
    assert dataset["prefix"] == "widgets_inc:"
    assert dataset["key_column"] is None
    assert dataset["row_count"] == 0
    assert dataset["live_rows"] == 0
    assert dataset["exists"] is True

    # Its own isolated SQLite file - created on the spot, not lazily.
    assert os.path.exists(dataset["db_path"])

    # ---- duplicate rejected (checked before register overwrites!)
    response = requests.post(
        f"{ROUTER_URL}/datasets",
        json={"name": "widgets inc"},
        timeout=5
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # ---- listing and single view agree
    listed = requests.get(f"{ROUTER_URL}/datasets", timeout=5).json()

    assert "widgets_inc" in [
        item["name"] for item in listed["datasets"]
    ]

    one = requests.get(
        f"{ROUTER_URL}/datasets/widgets_inc", timeout=5
    ).json()["dataset"]

    assert one["prefix"] == "widgets_inc:"
    assert one["live_rows"] == 0

    # ---- delete: manifest entry AND file are gone
    db_path = dataset["db_path"]

    response = requests.delete(
        f"{ROUTER_URL}/datasets/widgets_inc", timeout=5
    )

    assert response.status_code == 200
    assert response.json()["deleted"] is True

    assert not os.path.exists(db_path)
    assert "widgets_inc" not in router_module.database.registry.entries()
    assert requests.get(
        f"{ROUTER_URL}/datasets/widgets_inc", timeout=5
    ).status_code == 404


def test_create_rejects_empty_and_reserved_names(base):

    # Pydantic-level: names must be at least one character.
    response = requests.post(
        f"{ROUTER_URL}/datasets", json={"name": ""}, timeout=5
    )
    assert response.status_code == 422

    # "q"/"s" prefixes belong to the search cache - reserved.
    response = requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "q"}, timeout=5
    )
    assert response.status_code == 400
    assert "reserved" in response.json()["detail"]

    assert requests.get(
        f"{ROUTER_URL}/datasets/ghost", timeout=5
    ).status_code == 404


# =========================================================
# ENTRIES
# =========================================================

def test_add_entry_derives_keys_syncs_stats_and_purges_caches(base):

    requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "invoices"}, timeout=5
    )

    # ---- first entry: next-free-number key, schema appears --------
    response = requests.post(
        f"{ROUTER_URL}/datasets/invoices/entries",
        json={"value": {"amount": 10}},
        timeout=5
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "stored"
    assert body["key"] == "invoices:1"
    assert body["created"] is True
    assert body["row_count"] == 1
    assert body["schema"]["amount"]["type"] == "number"

    # ---- second entry continues the sequence ----------------------
    response = requests.post(
        f"{ROUTER_URL}/datasets/invoices/entries",
        json={"value": {"amount": 20}},
        timeout=5
    ).json()

    assert response["key"] == "invoices:2"
    assert response["row_count"] == 2

    # ---- the entity is now visible to the Search tab --------------
    entities = requests.get(
        f"{ROUTER_URL}/search/entities", timeout=5
    ).json()["entities"]

    assert "invoices" in entities
    assert entities["invoices"]["columns"] == ["amount"]
    assert entities["invoices"]["fields"]["amount"] == "number"
    assert any(
        details["kind"] == "range"
        for details in entities["invoices"]["filters"].values()
    )

    # ---- rows browsable, sorted, paged ----------------------------
    page = requests.get(
        f"{ROUTER_URL}/datasets/invoices/entries", timeout=5
    ).json()

    assert page["total"] == 2
    assert [row["key"] for row in page["rows"]] == [
        "invoices:1", "invoices:2"
    ]
    assert page["rows"][0]["value"] == {"amount": 10}

    paged = requests.get(
        f"{ROUTER_URL}/datasets/invoices/entries?limit=1&offset=1",
        timeout=5
    ).json()

    assert paged["total"] == 2
    assert [row["key"] for row in paged["rows"]] == ["invoices:2"]

    # ---- every mutation purges the entity's search caches ---------
    stale_key = "q:invoices:q=seeded"
    router_module.database.set(stale_key, {"items": [], "total": 0})

    response = requests.post(
        f"{ROUTER_URL}/datasets/invoices/entries",
        json={"value": {"amount": 30}},
        timeout=5
    ).json()

    assert response["row_count"] == 3
    assert "q:invoices:" in response["purged"]
    assert router_module.database.get(stale_key) is None


def test_add_entry_upserts_repeated_ids(base):

    requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "people"}, timeout=5
    )

    first = requests.post(
        f"{ROUTER_URL}/datasets/people/entries",
        json={"value": {"id": "abc", "name": "Ann"}},
        timeout=5
    ).json()

    assert first["key"] == "people:abc"
    assert first["created"] is True

    # Same id again: an UPSERT, never a duplicate key.
    second = requests.post(
        f"{ROUTER_URL}/datasets/people/entries",
        json={"value": {"id": "abc", "name": "Bea"}},
        timeout=5
    ).json()

    assert second["key"] == "people:abc"
    assert second["created"] is False
    assert second["row_count"] == 1

    rows = requests.get(
        f"{ROUTER_URL}/datasets/people/entries", timeout=5
    ).json()["rows"]

    assert len(rows) == 1
    assert rows[0]["value"]["name"] == "Bea"


def test_declared_key_column_governs_entry_keys(base):

    response = requests.post(
        f"{ROUTER_URL}/datasets",
        json={"name": "orders", "key_column": "order_id"},
        timeout=5
    )

    assert response.status_code == 200
    assert response.json()["dataset"]["key_column"] == "order_id"

    # The declared key field is mandatory for every entry.
    response = requests.post(
        f"{ROUTER_URL}/datasets/orders/entries",
        json={"value": {"total": 5}},
        timeout=5
    )

    assert response.status_code == 400
    assert "order_id" in response.json()["detail"]

    # With it: a fully deterministic key.
    response = requests.post(
        f"{ROUTER_URL}/datasets/orders/entries",
        json={"value": {"order_id": 7, "total": 5}},
        timeout=5
    ).json()

    assert response["key"] == "orders:7"
    assert response["created"] is True


def test_delete_entry_removes_the_row_everywhere(base):

    requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "ledger"}, timeout=5
    )

    for amount in (10, 20):
        requests.post(
            f"{ROUTER_URL}/datasets/ledger/entries",
            json={"value": {"amount": amount}},
            timeout=5
        )

    stale_key = "q:ledger:q=seeded"
    router_module.database.set(stale_key, {"items": []})

    # ---- keys from another dataset are refused --------------------
    response = requests.delete(
        f"{ROUTER_URL}/datasets/ledger/entries",
        params={"key": "other:1"},
        timeout=5
    )
    assert response.status_code == 400

    # ---- unknown key -> 404 ---------------------------------------
    response = requests.delete(
        f"{ROUTER_URL}/datasets/ledger/entries",
        params={"key": "ledger:999"},
        timeout=5
    )
    assert response.status_code == 404

    # ---- the real delete: row, stats, search caches ---------------
    response = requests.delete(
        f"{ROUTER_URL}/datasets/ledger/entries",
        params={"key": "ledger:1"},
        timeout=5
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "deleted"
    assert body["row_count"] == 1
    assert "q:ledger:" in body["purged"]

    assert requests.get(
        f"{ROUTER_URL}/db/ledger:1", timeout=5
    ).status_code == 404

    # The other entry and the system store are untouched.
    assert router_module.database.get("ledger:2") == {"amount": 20}
    assert router_module.database.get(stale_key) is None

    page = requests.get(
        f"{ROUTER_URL}/datasets/ledger/entries", timeout=5
    ).json()

    assert page["total"] == 1
    assert page["rows"][0]["key"] == "ledger:2"


def test_delete_dataset_purges_search_caches_and_file(base):

    requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "stock"}, timeout=5
    )
    requests.post(
        f"{ROUTER_URL}/datasets/stock/entries",
        json={"value": {"qty": 5}},
        timeout=5
    )

    stale_key = "q:stock:q=seeded"
    router_module.database.set(stale_key, {"items": []})

    db_path = router_module.database.registry.get("stock")["db_path"]

    response = requests.delete(f"{ROUTER_URL}/datasets/stock", timeout=5)

    assert response.status_code == 200

    body = response.json()

    assert body["deleted"] is True
    assert body["prefix"] == "stock:"
    assert "q:stock:" in body["purged"]
    assert "s:stock:" in body["purged"]

    # File, rows, search caches, manifest entry: all gone.
    assert not os.path.exists(db_path)
    assert router_module.database.get("stock:1") is None
    assert router_module.database.get(stale_key) is None
    assert "stock" not in router_module.database.registry.entries()

    assert requests.get(
        f"{ROUTER_URL}/datasets/stock", timeout=5
    ).status_code == 404

    assert "stock" not in requests.get(
        f"{ROUTER_URL}/search/entities", timeout=5
    ).json()["entities"]


# =========================================================
# DELETION SPLIT + NO-NODE WRITES
# =========================================================

def test_put_without_nodes_stores_with_a_warning(base):

    response = requests.put(
        f"{ROUTER_URL}/cache/scratch:1",
        json={"value": 5, "ttl": 30},
        timeout=5
    )

    # A scaled-down cluster is a state, not an error: the row is
    # stored and the response says so.
    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "stored"
    assert body["replicated_to"] == 0
    assert body["nodes"] == []
    assert body["source"] == "database"
    assert body["warning"] is not None
    assert body["dataset"] is None

    # The read path falls straight back to the database row.
    read = requests.get(f"{ROUTER_URL}/cache/scratch:1", timeout=5).json()

    assert read["value"] == 5
    assert read["source"] == "database"

    # Evict-only half of the deletion split: nobody holds the key
    # in a cache -> 404, and the database row still answers.
    response = requests.delete(f"{ROUTER_URL}/cache/scratch:1", timeout=5)

    assert response.status_code == 404
    assert requests.get(
        f"{ROUTER_URL}/db/scratch:1", timeout=5
    ).status_code == 200


def test_router_mutations_of_dataset_keys_sync_stats(base):

    requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "notes"}, timeout=5
    )
    requests.post(
        f"{ROUTER_URL}/datasets/notes/entries",
        json={"value": {"id": "n1", "text": "hello"}},
        timeout=5
    )

    # A raw /cache PUT that happens to write a dataset-owned key:
    # the hook re-syncs stats and purges that entity's caches.
    response = requests.put(
        f"{ROUTER_URL}/cache/notes:n2",
        json={"value": {"id": "n2", "text": "later"}},
        timeout=5
    )

    assert response.status_code == 200

    dataset = response.json()["dataset"]

    assert dataset["dataset"] == "notes"
    assert dataset["row_count"] == 2

    one = requests.get(
        f"{ROUTER_URL}/datasets/notes", timeout=5
    ).json()["dataset"]

    assert one["row_count"] == 2
    assert "text" in one["schema"]

    # ...and the same hook on the /db delete half.
    response = requests.delete(f"{ROUTER_URL}/db/notes:n2", timeout=5)

    assert response.status_code == 200
    assert response.json()["dataset"]["dataset"] == "notes"
    assert response.json()["dataset"]["row_count"] == 1

    assert requests.get(
        f"{ROUTER_URL}/db/notes:n2", timeout=5
    ).status_code == 404

    # Non-dataset keys report no dataset involvement at all.
    response = requests.put(
        f"{ROUTER_URL}/cache/scratch:x",
        json={"value": 1},
        timeout=5
    )

    assert response.json()["dataset"] is None


def test_search_cache_on_a_live_node_is_purged_after_mutation(base, node):

    # One real node in the ring: search result sets now get cached
    # ON it, so the purge has something visible to remove.
    router_module.configure_cluster([node])

    requests.post(
        f"{ROUTER_URL}/datasets", json={"name": "chirps"}, timeout=5
    )

    for text in ("alpha", "beta"):
        requests.post(
            f"{ROUTER_URL}/datasets/chirps/entries",
            json={"value": {"text": text}},
            timeout=5
        )

    # First search runs SQL and write-throughs the result set.
    first = requests.get(f"{ROUTER_URL}/search/chirps", timeout=5).json()

    assert first["source"] == "database"
    assert first["total"] == 2

    # The identical repeat is a pure cache hit off the node.
    second = requests.get(f"{ROUTER_URL}/search/chirps", timeout=5).json()

    assert second["source"] == "cache"
    assert second["total"] == 2

    # A new entry must purge that cached page - store row AND node
    # copy - so the next search re-runs the SQL.
    requests.post(
        f"{ROUTER_URL}/datasets/chirps/entries",
        json={"value": {"text": "gamma"}},
        timeout=5
    )

    third = requests.get(f"{ROUTER_URL}/search/chirps", timeout=5).json()

    assert third["source"] == "database"   # cache really was purged
    assert third["total"] == 3             # ...a stale page says 2


# =========================================================
# NODE-SIDE PRIMITIVES (prefix flush)
# =========================================================

def test_node_internal_prefix_flush(node):

    for key, value in (("ds:a", 1), ("ds:b", 2), ("other:1", 3)):
        response = requests.put(
            f"{node}/cache/{key}", json={"value": value}, timeout=5
        )
        assert response.status_code == 200

    # %3A is the quoted ":" - the prefix travels as a path segment.
    response = requests.delete(
        f"{node}/internal/prefix/{quote('ds:', safe='')}", timeout=5
    )

    assert response.status_code == 200
    assert response.json()["removed"] == 2

    assert requests.get(f"{node}/cache/ds:a", timeout=5).status_code == 404
    assert requests.get(f"{node}/cache/ds:b", timeout=5).status_code == 404

    # Prefixes that merely look similar survive.
    assert requests.get(
        f"{node}/cache/other:1", timeout=5
    ).status_code == 200


def test_distributed_flush_prefix_reaches_live_nodes(node):

    manager = NodeManager(
        nodes=[node],
        virtual_nodes=10,
        initial_state=NodeState.READY
    )
    cache = DistributedCache(manager)

    for key in ("p:x", "p:y", "z:1"):
        requests.put(f"{node}/cache/{key}", json={"value": key}, timeout=5)

    flushed = cache.flush_prefix("p:")

    # {node: removed_count} - only nodes that answered are listed.
    assert flushed == {node: 2}

    assert requests.get(f"{node}/cache/p:x", timeout=5).status_code == 404
    assert requests.get(f"{node}/cache/p:y", timeout=5).status_code == 404
    assert requests.get(f"{node}/cache/z:1", timeout=5).status_code == 200
