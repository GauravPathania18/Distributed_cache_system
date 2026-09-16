import time

from cache import LRUCache


def main():

    print("=" * 50)
    print("DISTRIBUTED CACHE - LAYER 1 DEMO")
    print("=" * 50)

    # ---------------------------------------------------------
    # Create cache
    # ---------------------------------------------------------

    cache = LRUCache(capacity=3)

    print("\nInitial cache:")
    print(cache)

    # ---------------------------------------------------------
    # SET
    # ---------------------------------------------------------

    print("\n--- SET operations ---")

    cache.set("A", 100)
    cache.set("B", 200)
    cache.set("C", 300)

    print(cache)

    # Expected:
    #
    # C -> B -> A
    #

    print("LRU order:", cache.keys())

    # ---------------------------------------------------------
    # GET
    # ---------------------------------------------------------

    print("\n--- GET A ---")

    value = cache.get("A")

    print("Value:", value)
    print("LRU order:", cache.keys())

    # Expected:
    #
    # A -> C -> B
    #

    # ---------------------------------------------------------
    # LRU EVICTION
    # ---------------------------------------------------------

    print("\n--- SET D ---")

    cache.set("D", 400)

    print(cache)

    print("LRU order:", cache.keys())

    print("GET B:", cache.get("B"))

    # B should be evicted because it was least recently used.
    #

    # ---------------------------------------------------------
    # GET C
    # ---------------------------------------------------------

    print("\n--- GET C ---")

    print("Value:", cache.get("C"))

    print("LRU order:", cache.keys())

    # ---------------------------------------------------------
    # TTL
    # ---------------------------------------------------------

    print("\n--- TTL TEST ---")

    cache.set(
        "temporary",
        "This will expire",
        ttl=2
    )

    print("Immediately:", cache.get("temporary"))

    print("Waiting 3 seconds...")

    time.sleep(3)

    print("After expiration:", cache.get("temporary"))

    # ---------------------------------------------------------
    # DELETE
    # ---------------------------------------------------------

    print("\n--- DELETE TEST ---")

    cache.set("X", 999)

    print("Before delete:", cache.keys())

    deleted = cache.delete("X")

    print("Deleted:", deleted)

    print("After delete:", cache.keys())

    # ---------------------------------------------------------
    # EXISTS
    # ---------------------------------------------------------

    print("\n--- EXISTS TEST ---")

    cache.set("hello", "world")

    print("hello exists:", cache.exists("hello"))
    print("random exists:", cache.exists("random"))

    # ---------------------------------------------------------
    # STATISTICS
    # ---------------------------------------------------------

    print("\n--- STATISTICS ---")

    print(cache.stats())

    print("\nFinal cache:")
    print(cache)


if __name__ == "__main__":
    main()