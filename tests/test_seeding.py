"""
Layer 6.5 - cold-start seeding (services/seeding.py).

These tests run against REAL SQLite stores (built in tmp_path):
the ordering claim - "top rows by the detected popularity column" -
is only meaningful if actual json_extract SQL is exercised.
"""

import pytest

from cache import LRUCache

from database.exceptions import DatabaseQueryError, DatabaseUnavailableError
from database.registry import DatasetRegistry
from database.multi_repository import MultiDatasetRepository
from database.repository import DatabaseRepository

from services.seeding import (
    CacheSeeder,
    dataset_keys,
    fetch_capacity,
    interleave,
    normalize_strategy,
    popularity_order,
    seed_budget,
    seed_keys,
)


# =========================================================
# FIXTURES - a hermetic registry + one SQLite file per dataset
# =========================================================

def _store(tmp_path, datasets):
    """Build a MultiDatasetRepository with registered datasets.

    `datasets` maps name -> {"rows": [(key, value), ...],
    "schema": {column: {"type": ...}}} - exactly what an import
    would have produced.
    """

    db_path = str(tmp_path / "cache.db")

    registry = DatasetRegistry(db_path=db_path)
    multi = MultiDatasetRepository(db_path=db_path, registry=registry)

    for name, spec in datasets.items():

        registry.register(
            name=name,
            row_count=len(spec["rows"]),
            schema=spec["schema"]
        )

        multi.repo_for(f"{name}:").set_many(spec["rows"])

    return multi


def _rating_dataset(name, count, offset=0):
    """`count` rows whose rating rises with the row number."""

    return {
        "rows": [
            (f"{name}:{i}", {"title": f"{name}-{i}", "rating": i + offset})
            for i in range(1, count + 1)
        ],
        "schema": {
            "title": {"type": "string", "distinct": count, "values": None},
            "rating": {"type": "number", "distinct": 0, "values": None},
        },
    }


# =========================================================
# STRATEGY + POPULARITY DETECTION
# =========================================================

def test_normalize_strategy_defaults_unknown_to_popular():

    assert normalize_strategy("popular") == "popular"
    assert normalize_strategy("RANDOM") == "random"
    assert normalize_strategy("  random  ") == "random"
    assert normalize_strategy("") == "popular"
    assert normalize_strategy("least_used") == "popular"
    assert normalize_strategy(None) == "popular"


def test_popularity_order_returns_the_desc_expression():

    spec = {
        "default_sort": "rating",
        "default_dir": "desc",
        "sorts": {"rating": "Aggregate rating"},
        "fields": {
            "Aggregate rating": "json_extract(value, '$.\"Aggregate rating\"')"
        },
    }

    assert popularity_order(spec) == (
        "json_extract(value, '$.\"Aggregate rating\"')"
    )


def test_popularity_order_is_none_without_a_signal():

    # No spec at all (schema-less dataset).
    assert popularity_order(None) is None

    # The default sort fell back to an ascending first column: it
    # ranks nothing worth seeding.
    assert popularity_order({
        "default_sort": "name",
        "default_dir": "asc",
        "sorts": {"name": "Name"},
        "fields": {"Name": "json_extract(value, '$.\"Name\"')"},
    }) is None


# =========================================================
# SELECTION AGAINST REAL SQLITE
# =========================================================

def test_popular_strategy_picks_top_rows_in_rank_order(tmp_path):

    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 10)})

    keys = seed_keys(multi, strategy="popular", per_dataset=4)

    assert keys == ["movies:10", "movies:9", "movies:8", "movies:7"]


def test_random_strategy_samples_requested_count(tmp_path):

    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 10)})

    keys = seed_keys(multi, strategy="random", per_dataset=5)

    assert len(keys) == 5
    assert set(keys) <= {f"movies:{i}" for i in range(1, 11)}

    # A limit above the row count simply returns every row.
    assert set(seed_keys(multi, strategy="random", per_dataset=99)) == {
        f"movies:{i}" for i in range(1, 11)
    }


def test_popular_falls_back_to_sampling_without_a_numeric_column(tmp_path):

    names = {
        "rows": [
            (f"names:{i}", {"name": f"entry-{i}"})
            for i in range(1, 9)
        ],
        "schema": {
            "name": {"type": "string", "distinct": 8, "values": None},
        },
    }

    multi = _store(tmp_path, {"names": names})

    keys = seed_keys(multi, strategy="popular", per_dataset=3)

    # No popularity signal -> uniform sample, still bounded.
    assert len(keys) == 3
    assert set(keys) <= {f"names:{i}" for i in range(1, 9)}


def test_pool_interleaves_datasets_in_popularity_order(tmp_path):

    multi = _store(tmp_path, {
        "alpha": _rating_dataset("alpha", 5),
        "beta": _rating_dataset("beta", 5),
    })

    keys = seed_keys(multi, strategy="popular", per_dataset=3)

    # Round-robin: any prefix (what a single node ends up walking)
    # still draws from BOTH datasets, hottest of each first.
    assert keys == [
        "alpha:5", "beta:5",
        "alpha:4", "beta:4",
        "alpha:3", "beta:3",
    ]


def test_seed_keys_is_empty_with_nothing_to_seed_from(tmp_path):

    # A plain DatabaseRepository has no registry at all.
    plain = DatabaseRepository(db_path=str(tmp_path / "plain.db"))
    assert seed_keys(plain) == []

    # A registry with no imports yet.
    empty = MultiDatasetRepository(db_path=str(tmp_path / "cache.db"))
    assert seed_keys(empty) == []

    # A non-positive limit never queries.
    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 3)})
    assert seed_keys(multi, per_dataset=0) == []


def test_one_odd_dataset_is_skipped_not_fatal():
    """A statement the query layer rejects skips that dataset quietly."""

    class RejectingRepo:
        def query(self, sql, params=(), max_rows=50):
            raise DatabaseQueryError("invalid query: no such column: nope")

    assert dataset_keys(RejectingRepo(), None, "popular", 10) == []


def test_a_storage_failure_propagates_from_selection():
    """Outage during selection = warm-up must fail and hold the gate."""

    class DownRepo:
        def query(self, sql, params=(), max_rows=50):
            raise DatabaseUnavailableError("simulated outage")

    with pytest.raises(DatabaseUnavailableError):
        dataset_keys(DownRepo(), None, "popular", 10)


# =========================================================
# BUDGET - seeds never overflow a node
# =========================================================

def test_seed_budget_is_a_clamped_fraction_of_capacity():

    assert seed_budget(200, 0.6) == 120
    assert seed_budget(100, 1.0) == 100
    assert seed_budget(200, 1.5) == 200      # clamped to the node
    assert seed_budget(200, -1) == 0
    assert seed_budget(100, 0) == 0
    assert seed_budget(0, 0.6) == 0
    assert seed_budget(None, 0.6) == 0


def test_seeder_budget_uses_the_nodes_real_capacity(tmp_path):

    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 3)})

    seeder = CacheSeeder(
        database=multi, fill_ratio=0.6, capacity_fetcher=lambda node: 200
    )

    assert seeder.budget("http://n1") == 120


def test_seeder_budget_falls_back_when_the_node_is_silent(tmp_path):

    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 3)})

    def unreachable(node):
        raise ConnectionError("node is down")

    # Unreachable node / node reporting 0 -> configured fallback.
    assert CacheSeeder(
        database=multi,
        fill_ratio=0.5,
        fallback_capacity=50,
        capacity_fetcher=unreachable,
    ).budget("http://n1") == 25

    assert CacheSeeder(
        database=multi,
        fill_ratio=0.5,
        fallback_capacity=50,
        capacity_fetcher=lambda node: 0,
    ).budget("http://n1") == 25

    # Default fallback (100) x default ratio (0.6).
    assert CacheSeeder(
        database=multi, capacity_fetcher=lambda node: None
    ).budget("http://n1") == 60


def test_fetch_capacity_is_zero_for_an_unreachable_node():

    # Nothing listens on that port: connection refused, not a hang.
    assert fetch_capacity("http://127.0.0.1:1", timeout=0.5) == 0


# =========================================================
# SEEDER SURFACE
# =========================================================

def test_seeder_normalizes_and_reports_its_policy(tmp_path):

    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 3)})

    seeder = CacheSeeder(
        database=multi,
        strategy="bogus",
        per_dataset=40,
        fill_ratio=0.5,
        fallback_capacity=60,
    )

    assert seeder.strategy == "popular"
    assert seeder.status() == {
        "strategy": "popular",
        "per_dataset": 40,
        "fill_ratio": 0.5,
        "fallback_capacity": 60,
    }


def test_seeder_candidates_delegate_to_the_store(tmp_path):

    multi = _store(tmp_path, {"movies": _rating_dataset("movies", 6)})

    seeder = CacheSeeder(database=multi, strategy="popular", per_dataset=2)

    assert seeder.candidates() == ["movies:6", "movies:5"]


def test_interleave_handles_empty_and_ragged_groups():

    assert interleave([]) == []
    assert interleave([[], []]) == []
    assert interleave([["a1", "a2"], ["b1"]]) == ["a1", "b1", "a2"]


# =========================================================
# LIFECYCLE CONTRACT: seeds are ordinary LRU citizens
# =========================================================

def test_seeded_keys_oust_via_lru_and_access_updates_priority():
    """The two promises seeding makes AFTER the initial load:

        - a request the cache cannot answer is fetched from the DB
          and inserted, and the LRU replaces the oldest row;
        - accessing a key raises its priority, so a frequently read
          seed survives while an untouched one is evicted first.
    """

    cache = LRUCache(3)

    # What a cold-start seed leaves behind: likely-used rows.
    cache.set("seed:a", 1)
    cache.set("seed:b", 2)
    cache.set("seed:c", 3)

    # seed:a is requested repeatedly -> its priority climbs.
    for _ in range(5):
        cache.get("seed:a")

    # A request the cache cannot answer: DB value inserted ->
    # LRU must replace the least-recently-used entry.
    cache.set("miss:d", 4)

    assert cache.get("seed:b") is None      # untouched seed evicted
    assert cache.get("seed:a") == 1         # frequent access won
    assert cache.get("seed:c") == 3
    assert cache.get("miss:d") == 4
