"""
MultiDatasetRepository: physical routing of keys into per-dataset
database files, with the system store (search cache + manual keys)
always available as the fallback.
"""

import os

from database import DatasetRegistry, MultiDatasetRepository
from database.repository import DatabaseRepository


def make_facade(tmp_path):
    db = str(tmp_path / "cache.db")
    registry = DatasetRegistry(db_path=db)
    return MultiDatasetRepository(db_path=db, registry=registry), registry


def register_users(registry):
    return registry.register(
        "users", prefix="user:", row_count=0, file_format="csv"
    )


# =========================================================
# ROUTING
# =========================================================

def test_unmatched_keys_go_to_the_system_store(tmp_path):

    facade, _ = make_facade(tmp_path)

    facade.set("q:keyword", '{"page": 1}')
    facade.set("manual:1", '{"x": 1}')

    assert facade.get("q:keyword") == '{"page": 1}'
    assert facade.system.count() == 2
    assert facade.count() == 2


def test_keys_route_into_their_own_database_file(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    facade.set("user:1", '{"name": "Ada"}')
    facade.set("q:kw", '{"page": 1}')

    # Each store only holds its own rows.
    assert facade.system.count() == 1
    dataset_repo = DatabaseRepository(db_path=entry["db_path"])
    assert dataset_repo.count() == 1
    assert dataset_repo.get("user:1") == '{"name": "Ada"}'
    assert dataset_repo.get("q:kw") is None

    # Facade reads still look up the right file transparently.
    assert facade.get("user:1") == '{"name": "Ada"}'
    assert facade.get("q:kw") == '{"page": 1}'
    assert facade.get("user:missing") is None


def test_longest_prefix_wins_between_datasets(tmp_path):

    facade, registry = make_facade(tmp_path)

    a = registry.register("base", prefix="data:", row_count=0)
    b = registry.register("extension", prefix="data:ext:", row_count=0)

    facade.set("data:1", "base-value")
    facade.set("data:ext:1", "extension-value")

    base_repo = DatabaseRepository(db_path=a["db_path"])
    ext_repo = DatabaseRepository(db_path=b["db_path"])

    assert base_repo.get("data:1") == "base-value"
    assert base_repo.get("data:ext:1") is None
    assert ext_repo.get("data:ext:1") == "extension-value"


def test_delete_routes_to_the_owning_store(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    facade.set("user:1", "v")
    facade.set("q:kw", "w")

    assert facade.delete("user:1") is True
    assert facade.delete("user:404") is False

    assert DatabaseRepository(db_path=entry["db_path"]).count() == 0
    assert facade.system.count() == 1  # search cache untouched


def test_set_many_splits_rows_across_stores(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    facade.set_many(dict({
        "user:1": "a",
        "user:2": "b",
        "q:kw": "c",
        "other:1": "d",
    }).items())

    assert DatabaseRepository(db_path=entry["db_path"]).count() == 2
    assert facade.system.count() == 2
    assert facade.get("user:2") == "b"
    assert facade.get("other:1") == "d"


# =========================================================
# MERGED VIEWS
# =========================================================

def test_keys_and_iter_keys_merge_and_sort_across_stores(tmp_path):

    facade, registry = make_facade(tmp_path)
    register_users(registry)

    facade.set("user:2", "b")
    facade.set("user:1", "a")
    facade.set("other:9", "z")

    assert facade.keys() == ["other:9", "user:1", "user:2"]
    assert list(facade.iter_keys()) == ["other:9", "user:1", "user:2"]
    assert facade.keys(2) == ["other:9", "user:1"]
    assert facade.count() == 3


def test_clear_wipes_every_store(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    facade.set("user:1", "a")
    facade.set("q:kw", "b")

    facade.clear()

    assert facade.system.count() == 0
    assert DatabaseRepository(db_path=entry["db_path"]).count() == 0


# =========================================================
# REPO LOOKUP HELPERS
# =========================================================

def test_repo_for_prefers_the_longest_matching_dataset(tmp_path):

    facade, registry = make_facade(tmp_path)

    registry.register("a", prefix="a:", row_count=0)
    registry.register("b", prefix="a:b:", row_count=0)

    assert facade.repo_for("a:b:1") is facade.repo_named("b")
    assert facade.repo_for("a:1") is facade.repo_named("a")
    assert facade.repo_for("unknown:1") is facade.system
    assert facade.repo_for("") is facade.system


def test_repos_matching_lists_system_when_nothing_prefix_matches(tmp_path):

    facade, registry = make_facade(tmp_path)

    register_users(registry)
    registry.register("orders", prefix="order:", row_count=0)

    assert [repo for repo in facade.repos_matching("user:")] == [
        facade.repo_named("users")
    ]
    assert [repo for repo in facade.repos_matching("user:")] != [facade.system]

    # Nothing starts with "xyz:" -> fall back to the system store only.
    assert [repo for repo in facade.repos_matching("xyz:")] == [facade.system]

    # Blank prefix matches every store (system first, then datasets).
    assert len(list(facade.repos_matching(""))) == 3
    assert list(facade.repos_matching(""))[0] is facade.system


def test_repos_lists_system_first(tmp_path):

    facade, registry = make_facade(tmp_path)
    register_users(registry)

    labels = [label for label, _ in facade.repos()]

    assert labels == ["system", "users"]


def test_repo_named_resolves_labels(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    assert facade.repo_named("") is facade.system
    assert facade.repo_named("system") is facade.system
    assert facade.repo_named("users").db_path == entry["db_path"]
    assert facade.repo_named("nope") is None


# =========================================================
# HEURISTIC QUERY ROUTING
# =========================================================

def test_query_infers_dataset_repo_from_query_parameters(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    facade.set("user:1", "in-dataset")
    facade.set("other:1", "in-system")

    result = facade.query(
        "SELECT key, value FROM cache_data WHERE key LIKE ?",
        ("user:%",)
    )

    # Only the dataset file can match -> that is where we read from.
    # (raw query() returns the stored JSON text)
    assert result["rows"] == [["user:1", '"in-dataset"']]

    # No prefix hint -> system store.
    result = facade.query(
        "SELECT key, value FROM cache_data WHERE key LIKE ?",
        ("other:%",)
    )
    assert result["rows"] == [["other:1", '"in-system"']]

    assert DatabaseRepository(db_path=entry["db_path"]).count() == 1


# =========================================================
# LIVE REGISTRY CHANGES
# =========================================================

def test_dataset_registered_after_construction_is_used_immediately(tmp_path):

    facade, registry = make_facade(tmp_path)

    facade.set("late:1", "v1")
    assert facade.system.count() == 1  # no dataset yet -> system

    registry.register("late", prefix="late:", row_count=0)

    facade.set("late:2", "v2")

    assert facade.system.count() == 1  # still only the old row
    assert facade.repo_named("late").count() == 1
    assert facade.get("late:2") == "v2"


def test_unregistered_dataset_disappears_from_the_map(tmp_path):

    facade, registry = make_facade(tmp_path)
    register_users(registry)

    assert facade.repo_named("users") is not None

    registry.unregister("users")

    assert facade.repo_named("users") is None

    # Writes with a now-unowned prefix fall back to the system store.
    facade.set("user:1", "fallback")
    assert facade.system.count() == 1


def test_dataset_db_files_are_created_lazily_on_first_touch(tmp_path):

    facade, registry = make_facade(tmp_path)
    entry = register_users(registry)

    assert not os.path.exists(entry["db_path"])

    facade.set("user:1", "v")

    assert os.path.exists(entry["db_path"])


# =========================================================
# LAYER 9 - PREFIX DELETES (search-cache purge)
# =========================================================

def test_delete_prefix_removes_only_matching_keys(tmp_path):

    facade, registry = make_facade(tmp_path)
    register_users(registry)

    facade.set("user:1", "a")
    facade.set("user:2", "b")
    facade.set("scratch:1", "c")

    assert facade.delete_prefix("user:") == 2

    assert facade.get("user:1") is None
    assert facade.get("user:2") is None

    # The system store was never touched.
    assert facade.system.get("scratch:1") == "c"

    # ...and the rows really left the dataset file.
    assert facade.repo_named("users").count() == 0


def test_delete_prefix_escapes_sql_wildcards(tmp_path):

    facade, registry = make_facade(tmp_path)
    register_users(registry)

    facade.set("user:a", 1)
    facade.set("user:ax", 2)
    facade.set("user:%", 3)

    # "%" in the prefix is a literal character, not LIKE wildcard:
    # only "user:%" itself may match.
    assert facade.delete_prefix("user:%") == 1

    assert facade.get("user:%") is None
    assert facade.get("user:a") == 1
    assert facade.get("user:ax") == 2


def test_delete_prefix_on_unknown_dataset_is_zero(tmp_path):

    facade, _ = make_facade(tmp_path)

    assert facade.delete_prefix("ghost:") == 0
