class _CacheStats:

    def size(self) -> int:
        """
        Return number of currently stored entries.

        Note:
            Expired entries that have not yet been accessed are still
            counted here because expiration is lazy.

        O(1)
        """

        with self.lock:
            return len(self.cache)

    def stats(self) -> dict:
        """
        Return cache statistics.
        """

        with self.lock:

            total_requests = self.hits + self.misses

            if total_requests == 0:
                hit_rate = 0.0
            else:
                hit_rate = (self.hits / total_requests) * 100

            return {
                "capacity": self.capacity,
                "size": len(self.cache),
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": round(hit_rate, 2)
            }

    def keys(self) -> list:
        """
        Return keys from most recently used to least recently used.

        Useful for debugging and understanding LRU behavior.
        """

        with self.lock:

            result = []

            current = self.head.next

            while current is not None and current != self.tail:

                result.append(current.key)

                current = current.next

            return result

    def __repr__(self) -> str:

        return (
            f"LRUCache("
            f"capacity={self.capacity}, "
            f"size={self.size()}, "
            f"keys={self.keys()}"
            f")"
        )
