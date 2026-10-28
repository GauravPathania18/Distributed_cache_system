import atexit
import os
import shutil
import tempfile

import pytest

from database.repository import DatabaseRepository
from services.cache_service import CacheService
from services.search import SearchService


# =========================================================
# FIXTURES
# =========================================================

# DatabaseRepository() defaults to the REAL data/cache.db and never
# reads CACHE_DB_PATH, so a bare constructor would clear the live
# database. Use a throwaway file instead - same contract conftest.py
# establishes for the router's own repository.
_SEARCH_DB_DIR = tempfile.mkdtemp(prefix="dcs-search-tests-")
_SEARCH_DB_PATH = os.path.join(_SEARCH_DB_DIR, "search.db")
atexit.register(shutil.rmtree, _SEARCH_DB_DIR, ignore_errors=True)


class DictCache:
    """In-memory stand-in for DistributedCache (get/set/delete)."""

    def __init__(self):
        self.store = {}
        self.node = "http://fake-node"

    def get(self, key):
        if key in self.store:
            return self.store[key], self.node
        return None, None

    def set(self, key, value, ttl=None, sequence=None):
        self.store[key] = value
        return [self.node]

    def delete(self, key, sequence=None):
        if key in self.store:
            del self.store[key]
            return [self.node]
        return []


PRODUCTS = [
    ("product:p-1", {"name": "Alpha widget",   "price": 10.0, "category": "tools",    "stock": 5,  "rating": 4.5}),
    ("product:p-2", {"name": "Beta widget",    "price": 20.0, "category": "tools",    "stock": 0,  "rating": 3.0}),
    ("product:p-3", {"name": "Gamma gadget",   "price": 30.0, "category": "electronics", "stock": 7, "rating": 5.0}),
    ("product:p-4", {"name": "Delta widget",   "price": 40.0, "category": "electronics", "stock": 0, "rating": 2.0}),
    ("product:p-5", {"name": "Epsilon doohickey", "price": 50.0, "category": "books",  "stock": 9,  "rating": 4.0}),
]

USERS = [
    ("user:1", {"name": "Ada Lovelace", "email": "ada@example.com", "age": 30, "city": "London", "signup_date": "2024-01-01", "active": True,  "score": 91.5}),
    ("user:2", {"name": "Alan Turing",  "email": "alan@example.com", "age": 41, "city": "London", "signup_date": "2023-05-05", "active": False, "score": 88.0}),
    ("user:3", {"name": "Grace Hopper", "email": "grace@example.com", "age": 55, "city": "New York", "signup_date": "2022-02-02", "active": True, "score": 95.0}),
]


# The service normally builds entities from the dataset registry;
# these tests inject explicit specs instead (SearchService(entities=...))
# to pin exact filters/params without importing files first.
#
# `fields` maps an output column name to the SQL expression that
# produces it. `filters` maps an accepted query-parameter name to a
# dict with:
#     field - the field name (looked up in `fields`)
#     kind  - "enum" | "range" | "min" | "bool"
#     min_param / max_param - query params for a "range"
#     test  - SQL boolean for a "bool" filter
# `sorts` is the ORDER BY whitelist: a sort name may only be one of
# these keys, which is why the column can be interpolated safely.

ENTITIES = {
    "products": {
        "prefix": "product:",
        "id_expr": "substr(key, 9)",
        "fields": {
            "name": "json_extract(value,'$.name')",
            "price": "json_extract(value,'$.price')",
            "category": "json_extract(value,'$.category')",
            "stock": "json_extract(value,'$.stock')",
            "rating": "json_extract(value,'$.rating')",
        },
        "text": ["name"],
        "filters": {
            "category": {"field": "category", "kind": "enum"},
            "price": {
                "field": "price",
                "kind": "range",
                "min_param": "pmin",
                "max_param": "pmax",
            },
            "rating": {"field": "rating", "kind": "min"},
            "instock": {
                "field": "stock",
                "kind": "bool",
                "test": "json_extract(value,'$.stock') > 0",
            },
        },
        "sorts": {
            "rating": "rating",
            "price": "price",
            "name": "name",
            "stock": "stock",
        },
        "default_sort": "rating",
        "default_dir": "desc",
        "suggest": ["category"],
    },
    "users": {
        "prefix": "user:",
        "id_expr": "substr(key, 6)",
        "fields": {
            "name": "json_extract(value,'$.name')",
            "email": "json_extract(value,'$.email')",
            "age": "json_extract(value,'$.age')",
            "city": "json_extract(value,'$.city')",
            "signup_date": "json_extract(value,'$.signup_date')",
            "active": "json_extract(value,'$.active')",
            "score": "json_extract(value,'$.score')",
        },
        "text": ["name", "city"],
        "filters": {
            "city": {"field": "city", "kind": "enum"},
            "age": {
                "field": "age",
                "kind": "range",
                "min_param": "amin",
                "max_param": "amax",
            },
            "score": {
                "field": "score",
                "kind": "range",
                "min_param": "smin",
                "max_param": "smax",
            },
            "active": {
                "field": "active",
                "kind": "bool",
                "test": "json_extract(value,'$.active') IN (1, 'true')",
            },
        },
        "sorts": {
            "score": "score",
            "age": "age",
            "name": "name",
            "signup_date": "signup_date",
        },
        "default_sort": "score",
        "default_dir": "desc",
        "suggest": ["city"],
    },
    "orders": {
        "prefix": "order:",
        "id_expr": "substr(key, 7)",
        "fields": {
            "user_id": "json_extract(value,'$.user_id')",
            "item": "json_extract(value,'$.item')",
            "quantity": "json_extract(value,'$.quantity')",
            "total": "json_extract(value,'$.total')",
            "status": "json_extract(value,'$.status')",
            "created_at": "json_extract(value,'$.created_at')",
        },
        "text": ["item"],
        "filters": {
            "status": {"field": "status", "kind": "enum"},
            "total": {
                "field": "total",
                "kind": "range",
                "min_param": "tmin",
                "max_param": "tmax",
            },
            "quantity": {
                "field": "quantity",
                "kind": "range",
                "min_param": "qmin",
                "max_param": "qmax",
            },
        },
        "sorts": {
            "total": "total",
            "quantity": "quantity",
            "created_at": "created_at",
            "item": "item",
        },
        "default_sort": "created_at",
        "default_dir": "desc",
        "suggest": ["status", "item"],
    },
}


def make_search():
    """Isolated DB (throwaway file) + real CacheService over a fake cache."""

    db = DatabaseRepository(db_path=_SEARCH_DB_PATH)
    db.clear()
    db.set_many(PRODUCTS + USERS)

    cache = DictCache()
    service = CacheService(cache=cache, database=db)

    search = SearchService(
        cache_service=service,
        distributed_cache=cache,
        single_flight=service.single_flight,
        circuit_breaker=service.circuit_breaker,
        database=db,
        telemetry=service.telemetry,
        ttl=60,
        entities=ENTITIES,
    )

    return search, service, cache, db


# =========================================================
# CORE CACHING BEHAVIOUR
# =========================================================

def test_first_search_runs_sql_then_caches():

    search, service, cache, db = make_search()

    first = search.search("products", {"q": "widget"})

    assert first["source"] == "database"
    assert first["total"] == 3

    second = search.search("products", {"q": "widget"})

    assert second["source"] == "cache"
    assert second["total"] == 3
    assert second["node"] == "http://fake-node"


def test_repeated_search_is_pure_cache_hit():

    search, service, cache, db = make_search()

    for _ in range(5):
        search.search("products", {"q": "widget"})

    telemetry = service.telemetry.stats()

    assert telemetry["search_database"] == 1
    assert telemetry["search_cache"] == 4


def test_search_result_set_is_replicated_via_write_through():

    search, service, cache, db = make_search()

    result = search.search("products", {"q": "widget"})

    # The write-through wrote a DB row for the query key too.
    assert db.exists(result["cache_key"]) is True

    # And it is visible in the cache (fake node holds it).
    assert result["cache_key"] in cache.store


def test_different_pages_use_different_cache_keys():

    search, service, cache, db = make_search()

    p1 = search.search("products", {"q": "widget", "page": 1})
    p2 = search.search("products", {"q": "widget", "page": 2})

    assert p1["cache_key"] != p2["cache_key"]


def test_cache_key_is_url_path_safe():

    search, service, cache, db = make_search()

    result = search.search("products", {"q": "a/b c;d=e", "category": "tools"})

    assert "/" not in result["cache_key"]
    assert " " not in result["cache_key"]


def test_cache_key_is_canonical_case_insensitive():

    search, service, cache, db = make_search()

    a = search.search("products", {"q": "Widget"})
    b = search.search("products", {"q": "widget"})

    assert a["cache_key"] == b["cache_key"]


# =========================================================
# FILTERS, SORT, PAGINATION
# =========================================================

def test_keyword_multi_term_is_anded():

    search, service, cache, db = make_search()

    both = search.search("products", {"q": "alpha widget"})
    only_one = search.search("products", {"q": "alpha gadget"})

    assert both["total"] == 1
    assert only_one["total"] == 0


def test_category_filter():

    search, service, cache, db = make_search()

    result = search.search("products", {"category": "tools"})

    assert result["total"] == 2
    assert all(item["category"] == "tools" for item in result["items"])


def test_price_range_filter():

    search, service, cache, db = make_search()

    result = search.search("products", {"pmin": 25, "pmax": 45})

    assert result["total"] == 2
    prices = sorted(item["price"] for item in result["items"])
    assert prices == [30.0, 40.0]


def test_rating_min_filter():

    search, service, cache, db = make_search()

    result = search.search("products", {"rating": 4})

    assert result["total"] == 3
    assert all(item["rating"] >= 4 for item in result["items"])


def test_instock_filter():

    search, service, cache, db = make_search()

    result = search.search("products", {"instock": "true"})

    assert result["total"] == 3
    assert all(item["stock"] > 0 for item in result["items"])


def test_sort_by_price_desc():

    search, service, cache, db = make_search()

    result = search.search("products", {"sort": "price", "dir": "desc"})

    prices = [item["price"] for item in result["items"]]
    assert prices == sorted(prices, reverse=True)


def test_bad_sort_falls_back_to_default():

    search, service, cache, db = make_search()

    result = search.search("products", {"sort": "price; DROP TABLE cache_data", "dir": "asc"})

    # Sort silently falls back to the entity default; nothing breaks.
    assert result["total"] == 5
    assert db.count() > 0


def test_pagination_returns_disjoint_pages():

    search, service, cache, db = make_search()

    p1 = search.search("products", {"page": 1, "page_size": 2, "sort": "price", "dir": "asc"})
    p2 = search.search("products", {"page": 2, "page_size": 2, "sort": "price", "dir": "asc"})

    assert len(p1["items"]) == 2
    assert len(p2["items"]) == 2
    assert p1["items"][0]["id"] != p2["items"][0]["id"]
    assert p1["page_count"] == 3


def test_page_size_is_clamped():

    search, service, cache, db = make_search()

    result = search.search("products", {"page_size": 99999})

    assert result["page_size"] <= 100


def test_users_search_filters():

    search, service, cache, db = make_search()

    result = search.search("users", {"city": "London"})

    assert result["total"] == 2
    assert all(item["city"] == "London" for item in result["items"])


def test_users_active_filter():

    search, service, cache, db = make_search()

    result = search.search("users", {"active": "true"})

    assert result["total"] == 2
    assert all(item["active"] for item in result["items"])


def test_envelope_reports_db_took_ms():

    search, service, cache, db = make_search()

    result = search.search("products", {"q": "widget"})

    assert "db_took_ms" in result
    assert "took_ms" in result
    assert result["cached_at"] > 0


# =========================================================
# SUGGEST / BROWSE / ERRORS
# =========================================================

def test_suggest_returns_distinct_values_and_caches():

    search, service, cache, db = make_search()

    first = search.suggest("products", "category", "")
    assert set(first["values"]) == {"tools", "electronics", "books"}
    assert first["source"] == "database"

    second = search.suggest("products", "category", "")
    assert second["source"] == "cache"


def test_suggest_filtered_by_prefix():

    search, service, cache, db = make_search()

    result = search.suggest("products", "category", "ele")
    assert result["values"] == ["electronics"]


def test_suggest_rejects_unknown_field():

    search, service, cache, db = make_search()

    with pytest.raises(ValueError):
        search.suggest("products", "price", "")


def test_browse_lists_keys_by_prefix():

    search, service, cache, db = make_search()

    result = search.browse("user:", limit=10)

    assert result["truncated"] is False
    assert len(result["keys"]) == 3
    assert all(k["key"].startswith("user:") for k in result["keys"])


def test_browse_is_not_cached():

    search, service, cache, db = make_search()

    search.browse("user:", limit=10)
    telemetry = service.telemetry.stats()

    # Browsing never touches the search counters - it is an
    # inspector tool, not a cached query.
    assert telemetry["search_cache"] == 0
    assert telemetry["search_database"] == 0


def test_unknown_entity_raises():

    search, service, cache, db = make_search()

    with pytest.raises(ValueError):
        search.search("aliens", {})


def test_entities_metadata_shape():

    search, service, cache, db = make_search()

    meta = search.entities()

    assert set(meta) == {"products", "users", "orders"}
    assert "category" in meta["products"]["filters"]
    assert meta["products"]["filters"]["price"]["kind"] == "range"
    assert meta["products"]["filters"]["price"]["min_param"] == "pmin"
    assert "price" in meta["products"]["sorts"]
    assert meta["products"]["default_sort"] == "rating"


# =========================================================
# REAL-WORLD SCHEMAS: column names with spaces / mixed case
# =========================================================
#
# build_entities() derives SQL straight from a sniffed schema, and
# real CSVs carry columns like "Restaurant Name" or "Country Code".
# Those are invalid as bare SQL identifiers, so the generated
# SELECT aliases MUST be quoted. Before the fix, any such dataset
# failed with a syntax error reported as a fake "database
# unavailable".

from services.search import build_entities  # noqa: E402

_RESTAURANT_ROWS = [
    ("rest:1", {"Restaurant Name": "Trattoria", "Country Code": 1,
                "Aggregate rating": 4.5, "Cuisines": "Italian"}),
    ("rest:2", {"Restaurant Name": "Sakura", "Country Code": 3,
                "Aggregate rating": 4.1, "Cuisines": "Japanese"}),
    ("rest:3", {"Restaurant Name": "El Farol", "Country Code": 2,
                "Aggregate rating": 3.9, "Cuisines": "Mexican"}),
]

_RESTAURANT_SCHEMA = {
    "Restaurant Name": {"type": "string", "distinct": 3,
                        "values": ["El Farol", "Sakura", "Trattoria"]},
    "Country Code": {"type": "number", "distinct": 3, "values": None},
    "Aggregate rating": {"type": "number", "distinct": 3, "values": None},
    "Cuisines": {"type": "string", "distinct": 3,
                 "values": ["Italian", "Japanese", "Mexican"]},
}


def make_spaced_search():
    """SearchService over a schema whose columns contain spaces."""

    db = DatabaseRepository(db_path=_SEARCH_DB_PATH)
    db.clear()
    db.set_many(_RESTAURANT_ROWS)

    entries = {
        "restaurants": {
            "name": "restaurants",
            "prefix": "rest:",
            "schema": _RESTAURANT_SCHEMA,
        }
    }

    specs = build_entities(entries)

    cache = DictCache()
    service = CacheService(cache=cache, database=db)

    search = SearchService(
        cache_service=service,
        distributed_cache=cache,
        single_flight=service.single_flight,
        circuit_breaker=service.circuit_breaker,
        database=db,
        telemetry=service.telemetry,
        ttl=60,
        entities=specs,
    )

    return search, db


def test_build_entities_keeps_space_column_names():
    """Columns with spaces survive into fields/sorts (quoted in SQL)."""

    _, db = make_spaced_search()

    specs = build_entities({"restaurants": {
        "prefix": "rest:", "schema": _RESTAURANT_SCHEMA}})

    spec = specs["restaurants"]

    assert "Restaurant Name" in spec["fields"]
    assert "Aggregate rating" in spec["fields"]
    # The sort map is keyed by a lower-cased, underscored alias but
    # still points at the raw column.
    assert spec["sorts"]["aggregate_rating"] == "Aggregate rating"
    assert spec["sorts"]["restaurant_name"] == "Restaurant Name"


def test_search_over_space_named_columns():
    """A plain search over space-named columns returns every row."""

    search, db = make_spaced_search()

    result = search.search("restaurants", {"page_size": "10"})

    assert result["total"] == 3
    assert len(result["items"]) == 3


def test_search_items_expose_raw_column_keys():
    """Item keys equal the raw column names the UI reads verbatim."""

    search, db = make_spaced_search()

    result = search.search("restaurants", {"page_size": "10"})

    first = result["items"][0]

    assert "Restaurant Name" in first
    assert "Aggregate rating" in first
    assert first["Restaurant Name"] in {"Trattoria", "Sakura", "El Farol"}


def test_sort_by_space_named_column():
    """ORDER BY on a space-named column works and sorts correctly."""

    search, db = make_spaced_search()

    result = search.search(
        "restaurants",
        {"sort": "aggregate_rating", "dir": "desc", "page_size": "10"},
    )

    ratings = [item["Aggregate rating"] for item in result["items"]]

    assert ratings == sorted(ratings, reverse=True)


def test_keyword_search_over_space_named_text_field():
    """Keyword search matches a space-named text column."""

    search, db = make_spaced_search()

    result = search.search("restaurants", {"q": "sakura"})

    assert result["total"] == 1
    assert result["items"][0]["Restaurant Name"] == "Sakura"


def test_range_filter_on_space_named_number_column():
    """A min/max range over a space-named number column filters rows."""

    search, db = make_spaced_search()

    result = search.search(
        "restaurants",
        {"country_code_min": "2", "country_code_max": "3",
         "page_size": "10"},
    )

    assert result["total"] == 2


def test_enum_facet_on_space_named_column():
    """An enum facet over a space-named column narrows to one value."""

    search, db = make_spaced_search()

    result = search.search("restaurants", {"cuisines": "Japanese"})

    assert result["total"] == 1
    assert result["items"][0]["Cuisines"] == "Japanese"


def test_suggest_on_space_named_column():
    """Distinct-value suggest works for a space-named field."""

    search, db = make_spaced_search()

    result = search.suggest("restaurants", "Cuisines", "")

    assert "Italian" in result["values"]


def test_entities_metadata_for_space_columns():
    """entities() reports raw column names and types for the UI."""

    search, db = make_spaced_search()

    meta = search.entities()["restaurants"]

    assert "Restaurant Name" in meta["columns"]
    assert meta["fields"]["Aggregate rating"] == "number"
    assert "aggregate_rating" in meta["sorts"]


# =========================================================
# QUERY ERRORS vs AVAILABILITY ERRORS
# =========================================================

from database.exceptions import (  # noqa: E402
    DatabaseQueryError,
    DatabaseUnavailableError,
)
from services.circuit_breaker import CircuitBreaker  # noqa: E402


def test_malformed_sql_raises_query_error_not_unavailable():
    """A syntax error is a DatabaseQueryError, not an outage."""

    _, db = make_spaced_search()

    with pytest.raises(DatabaseQueryError):
        db.query("SELECT nonsense FROM cache_data WHERE")


def test_unknown_column_raises_query_error():
    """Selecting a missing column is a DatabaseQueryError."""

    _, db = make_spaced_search()

    with pytest.raises(DatabaseQueryError):
        db.query("SELECT no_such_column FROM cache_data")


def test_valid_query_does_not_raise():
    """A well-formed query still returns rows normally."""

    _, db = make_spaced_search()

    result = db.query("SELECT key FROM cache_data LIMIT 1", max_rows=1)

    assert len(result["rows"]) == 1


def test_query_error_does_not_trip_circuit_breaker():
    """A bad statement must not count as a storage failure."""

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30)

    def bad_sql():
        raise DatabaseQueryError("invalid query: syntax error")

    with pytest.raises(DatabaseQueryError):
        cb.call(bad_sql)

    # Still closed: the breaker saw no storage failure.
    assert cb.get_state() == CircuitBreaker.CLOSED
    assert cb.get_stats()["failure_count"] == 0


def test_storage_failure_still_trips_circuit_breaker():
    """Real availability errors keep counting toward the threshold."""

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30)

    def down():
        raise DatabaseUnavailableError("database unavailable")

    with pytest.raises(DatabaseUnavailableError):
        cb.call(down)

    assert cb.get_state() == CircuitBreaker.OPEN
