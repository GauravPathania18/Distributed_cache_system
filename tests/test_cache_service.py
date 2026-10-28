import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from database.exceptions import DatabaseUnavailableError
from services.cache_service import CacheService
from services.circuit_breaker import CircuitBreaker


# =========================================================
# FAKES
# =========================================================

class FakeCache:
    """In-memory stand-in for the distributed cache client."""

    def __init__(self):
        self.store = {}
        self.set_calls = 0

    def get(self, key):
        if key in self.store:
            return self.store[key], "http://node-1"
        return None, None

    def set(self, key, value, ttl=None, sequence=None):
        self.store[key] = value
        self.set_calls += 1
        return ["http://node-1", "http://node-2"]

    def delete(self, key, sequence=None):
        if key in self.store:
            del self.store[key]
            return ["http://node-1", "http://node-2"]
        return []


class FakeDatabase:
    """In-memory stand-in for the SQLite repository."""

    def __init__(self):
        self.store = {}
        self.get_calls = 0

    def get(self, key):
        self.get_calls += 1
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, key):
        if key in self.store:
            del self.store[key]
            return True
        return False


class SlowDatabase(FakeDatabase):
    """Database that slows down reads to expose stampede behavior."""

    def get(self, key):
        self.get_calls += 1
        time.sleep(0.1)
        return self.store.get(key)


class FailingDatabase:

    def __init__(self):
        self.get_calls = 0
        self.calls = 0

    def get(self, key):
        self.get_calls += 1
        self.calls += 1
        raise DatabaseUnavailableError(
            f"database unavailable during get({key!r})"
        )

    def set(self, key, value):
        self.calls += 1
        raise DatabaseUnavailableError(
            f"database unavailable during set({key!r})"
        )

    def delete(self, key):
        self.calls += 1
        raise DatabaseUnavailableError(
            f"database unavailable during delete({key!r})"
        )


# =========================================================
# GET
# =========================================================

def test_get_cache_hit_does_not_touch_database():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    cache.store["A"] = 100

    result = service.get("A")

    assert result["value"] == 100
    assert result["source"] == "cache"
    assert result["node"] == "http://node-1"
    assert database.get_calls == 0


def test_get_cache_miss_falls_back_to_database_and_populates_cache():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    database.store["A"] = 100

    result = service.get("A")

    assert result["value"] == 100
    assert result["source"] == "database"
    assert result["node"] is None

    # The database value should now be cached.
    assert cache.store["A"] == 100


def test_get_cache_miss_and_database_miss_returns_none():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    assert service.get("missing") is None
    assert database.get_calls == 1


def test_second_get_is_a_cache_hit_after_population():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    database.store["A"] = 100

    first = service.get("A")
    second = service.get("A")

    assert first["source"] == "database"
    assert second["source"] == "cache"
    assert database.get_calls == 1


# =========================================================
# SET
# =========================================================

def test_set_writes_to_database_and_cache():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    result = service.set("A", 100)

    assert database.store["A"] == 100
    assert cache.store["A"] == 100
    assert len(result["nodes"]) == 2


# =========================================================
# DELETE
# =========================================================

def test_delete_removes_from_database_and_cache():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    database.store["A"] = 100
    cache.store["A"] = 100

    result = service.delete("A")

    assert result["deleted"] is True
    assert "A" not in database.store
    assert "A" not in cache.store


def test_delete_missing_key_reports_not_deleted():

    cache = FakeCache()
    database = FakeDatabase()
    service = CacheService(cache=cache, database=database)

    result = service.delete("missing")

    assert result["deleted"] is False


# =========================================================
# STAMPEDE PROTECTION
# =========================================================

def test_concurrent_misses_hit_database_only_once():

    cache = FakeCache()
    database = SlowDatabase()
    service = CacheService(cache=cache, database=database)

    database.store["A"] = 100

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(
            executor.map(
                lambda _: service.get("A"),
                range(5)
            )
        )

    # Every caller gets the value...
    assert all(result["value"] == 100 for result in results)

    # ...but the database was queried exactly once (single-flight).
    assert database.get_calls == 1


# =========================================================
# DATABASE FAILURES (Layer 5.7)
# =========================================================

def test_get_cache_miss_db_failure_raises_error():

    cache = FakeCache()
    database = FailingDatabase()
    service = CacheService(cache=cache, database=database)

    with pytest.raises(DatabaseUnavailableError):
        service.get("A")


def test_get_cache_hit_still_works_when_db_down():

    cache = FakeCache()
    database = FailingDatabase()
    service = CacheService(cache=cache, database=database)

    cache.store["A"] = 100

    result = service.get("A")

    assert result["value"] == 100
    assert result["source"] == "cache"
    assert result["node"] == "http://node-1"

    # The cache hit never touches the database recovery probe.
    assert database.get_calls == 0


def test_set_db_failure_raises_error():

    cache = FakeCache()
    database = FailingDatabase()
    service = CacheService(cache=cache, database=database)

    with pytest.raises(DatabaseUnavailableError):
        service.set("A", 100)

    # The write must never reach the cache if the DB rejected it.
    assert "A" not in cache.store


def test_delete_db_failure_raises_error():

    cache = FakeCache()
    database = FailingDatabase()
    service = CacheService(cache=cache, database=database)

    cache.store["A"] = 100

    with pytest.raises(DatabaseUnavailableError):
        service.delete("A")


def test_circuit_breaker_opens_after_repeated_db_failures():

    cache = FakeCache()
    database = FailingDatabase()
    breaker = CircuitBreaker(failure_threshold=2)
    service = CacheService(cache=cache, database=database, circuit_breaker=breaker)

    # Two failures trip the breaker.
    for _ in range(2):
        with pytest.raises(DatabaseUnavailableError):
            service.get("A")

    assert breaker.get_state() == "open"

    # While open, the database is never called again.
    calls_after_open = database.get_calls

    for _ in range(3):
        with pytest.raises(DatabaseUnavailableError):
            service.get("B")

    assert database.get_calls == calls_after_open