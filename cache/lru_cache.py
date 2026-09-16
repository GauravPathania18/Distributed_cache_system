import time
import threading
from typing import Any, Callable, Dict, Optional

from .node import Node


class _LoadState:

    def __init__(self):
        self.done = threading.Event()
        self.error: Optional[BaseException] = None


class LRUCache:
    """
    Thread-safe in-memory LRU cache with TTL support.

    Data structures:

        HashMap:
            key -> Node

        Doubly Linked List:
            HEAD <-> Most Recently Used ... Least Recently Used <-> TAIL

    Complexity:

        get()    -> O(1)
        set()    -> O(1)
        delete() -> O(1)

    TTL expiration is checked lazily whenever an entry is accessed.
    """

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Cache capacity must be greater than 0")

        self.capacity = capacity

        # HashMap:
        # key -> Node
        self.cache: Dict[str, Node] = {}

        # Dummy/sentinel nodes.
        #
        # HEAD = beginning of linked list
        # TAIL = end of linked list
        #
        # Real nodes are inserted between them.
        self.head = Node("__HEAD__", None)
        self.tail = Node("__TAIL__", None)

        self.head.next = self.tail
        self.tail.prev = self.head

        # Statistics
        self.hits = 0
        self.misses = 0
        self.evictions = 0

        # Protect cache operations from concurrent threads.
        self.lock = threading.RLock()

        # One in-flight loader is allowed per key to prevent cache stampedes.
        self._loads: Dict[str, _LoadState] = {}

    # ============================================================
    # INTERNAL LINKED LIST OPERATIONS
    # ============================================================

    def _remove_node(self, node: Node) -> None:
        """
        Remove a node from the doubly linked list.

        O(1)
        """

        previous = node.prev
        next_node = node.next

        if previous is not None:
            previous.next = next_node

        if next_node is not None:
            next_node.prev = previous

        node.prev = None
        node.next = None

    def _add_to_front(self, node: Node) -> None:
        """
        Insert node immediately after HEAD.

        The front represents the most recently used item.

        O(1)
        """

        first_node = self.head.next

        node.prev = self.head
        node.next = first_node

        self.head.next = node

        if first_node is not None:
            first_node.prev = node

    def _move_to_front(self, node: Node) -> None:
        """
        Move an existing node to the front.

        O(1)
        """

        self._remove_node(node)
        self._add_to_front(node)

    def _remove_lru(self) -> Optional[Node]:
        """
        Remove and return the least recently used node.

        The LRU node is immediately before TAIL.

        O(1)
        """

        lru_node = self.tail.prev

        if lru_node is None or lru_node == self.head:
            return None

        self._remove_node(lru_node)

        return lru_node

    # ============================================================
    # PUBLIC API
    # ============================================================

    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from the cache.

        Returns:
            value if found and not expired
            None if missing/expired

        O(1)
        """

        with self.lock:

            node = self.cache.get(key)

            # Cache MISS
            if node is None:
                self.misses += 1
                return None

            # TTL check
            current_time = time.monotonic()

            if node.is_expired(current_time):

                # Remove expired item
                self._remove_node(node)
                del self.cache[key]

                self.misses += 1

                return None

            # Cache HIT
            self.hits += 1

            # Recently accessed -> move to front
            self._move_to_front(node)

            return node.value

    def get_or_set(
        self,
        key: str,
        loader: Callable[[], Any],
        ttl: Optional[float] = None
    ) -> Any:
        """Return a cached value, loading it once when it is missing.

        Concurrent callers for the same missing key wait for the first
        caller's loader instead of running the loader themselves.
        """

        while True:
            cached_value = self.get(key)

            if cached_value is not None:
                return cached_value

            with self.lock:
                load_state = self._loads.get(key)

                if load_state is None:
                    load_state = _LoadState()
                    self._loads[key] = load_state
                    is_loader = True
                else:
                    is_loader = False

            if is_loader:
                break

            load_state.done.wait()

            if load_state.error is not None:
                raise load_state.error

        try:
            value = loader()
            self.set(key, value, ttl=ttl)
            return value
        except BaseException as error:
            load_state.error = error
            raise
        finally:
            with self.lock:
                del self._loads[key]
                load_state.done.set()

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None
    ) -> None:
        """
        Insert or update a cache entry.

        Args:
            key:
                Cache key.

            value:
                Value to store.

            ttl:
                Time-to-live in seconds.

                None -> never expires.
                10   -> expires after 10 seconds.

        O(1)
        """

        if ttl is not None and ttl <= 0:
            raise ValueError("TTL must be greater than 0")

        with self.lock:

            expires_at = None

            if ttl is not None:
                expires_at = time.monotonic() + ttl

            # ----------------------------------------------------
            # KEY ALREADY EXISTS
            # ----------------------------------------------------

            existing_node = self.cache.get(key)

            if existing_node is not None:

                existing_node.value = value
                existing_node.expires_at = expires_at

                # Updating an item means it was recently used.
                self._move_to_front(existing_node)

                return

            # ----------------------------------------------------
            # NEW KEY
            # ----------------------------------------------------

            new_node = Node(
                key=key,
                value=value,
                expires_at=expires_at
            )

            self.cache[key] = new_node

            self._add_to_front(new_node)

            # ----------------------------------------------------
            # CACHE FULL
            # ----------------------------------------------------

            if len(self.cache) > self.capacity:

                lru_node = self._remove_lru()

                if lru_node is not None:

                    del self.cache[lru_node.key]

                    self.evictions += 1

    def delete(self, key: str) -> bool:
        """
        Delete an entry.

        Returns:
            True  -> entry existed and was deleted
            False -> entry didn't exist

        O(1)
        """

        with self.lock:

            node = self.cache.get(key)

            if node is None:
                return False

            self._remove_node(node)

            del self.cache[key]

            return True

    def exists(self, key: str) -> bool:
        """
        Check whether a valid entry exists.

        Note:
            This does not increment hit/miss statistics.

        O(1)
        """

        with self.lock:

            node = self.cache.get(key)

            if node is None:
                return False

            if node.is_expired(time.monotonic()):

                self._remove_node(node)
                del self.cache[key]

                return False

            return True

    def clear(self) -> None:
        """
        Remove every cache entry.

        O(n)
        """

        with self.lock:

            self.cache.clear()

            self.head.next = self.tail
            self.tail.prev = self.head

    # ============================================================
    # CACHE INFORMATION
    # ============================================================

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

    # ============================================================
    # DEBUGGING / VISUALIZATION
    # ============================================================

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