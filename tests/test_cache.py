import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from cache import LRUCache


def test_set_and_get():

    cache = LRUCache(3)

    cache.set("A", 100)

    assert cache.get("A") == 100


def test_missing_key():

    cache = LRUCache(3)

    assert cache.get("A") is None


def test_update_existing_key():

    cache = LRUCache(3)

    cache.set("A", 100)
    cache.set("A", 200)

    assert cache.get("A") == 200


def test_lru_eviction():

    cache = LRUCache(3)

    cache.set("A", 1)
    cache.set("B", 2)
    cache.set("C", 3)

    # A becomes recently used
    cache.get("A")

    # Order:
    #
    # A -> C -> B
    #

    cache.set("D", 4)

    # B should be evicted.

    assert cache.get("B") is None

    assert cache.get("A") == 1
    assert cache.get("C") == 3
    assert cache.get("D") == 4


def test_lru_order():

    cache = LRUCache(3)

    cache.set("A", 1)
    cache.set("B", 2)
    cache.set("C", 3)

    assert cache.keys() == ["C", "B", "A"]

    cache.get("A")

    assert cache.keys() == ["A", "C", "B"]

    cache.get("B")

    assert cache.keys() == ["B", "A", "C"]


def test_delete():

    cache = LRUCache(3)

    cache.set("A", 100)

    assert cache.delete("A") is True

    assert cache.get("A") is None

    assert cache.delete("A") is False


def test_exists():

    cache = LRUCache(3)

    cache.set("A", 100)

    assert cache.exists("A") is True

    assert cache.exists("B") is False


def test_ttl():

    cache = LRUCache(3)

    cache.set(
        "A",
        100,
        ttl=1
    )

    assert cache.get("A") == 100

    time.sleep(1.2)

    assert cache.get("A") is None


def test_expired_entry_is_deleted():

    cache = LRUCache(3)

    cache.set(
        "A",
        100,
        ttl=1
    )

    time.sleep(1.2)

    cache.get("A")

    assert cache.exists("A") is False
    assert cache.size() == 0


def test_statistics():

    cache = LRUCache(3)

    cache.set("A", 100)

    cache.get("A")  # hit

    cache.get("B")  # miss

    stats = cache.stats()

    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 50.0


def test_eviction_statistics():

    cache = LRUCache(2)

    cache.set("A", 1)
    cache.set("B", 2)

    cache.set("C", 3)

    stats = cache.stats()

    assert stats["evictions"] == 1
    assert stats["size"] == 2


def test_clear():

    cache = LRUCache(3)

    cache.set("A", 1)
    cache.set("B", 2)
    cache.set("C", 3)

    cache.clear()

    assert cache.size() == 0
    assert cache.keys() == []


def test_capacity_validation():

    try:
        LRUCache(0)
        assert False
    except ValueError:
        assert True


def test_get_or_set_prevents_cache_stampede():

    cache = LRUCache(3)
    loader_calls = 0
    loader_lock = Lock()

    def loader():
        nonlocal loader_calls

        time.sleep(0.1)

        with loader_lock:
            loader_calls += 1

        return 100

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(
            executor.map(
                lambda _: cache.get_or_set("A", loader),
                range(5)
            )
        )

    assert results == [100] * 5
    assert loader_calls == 1