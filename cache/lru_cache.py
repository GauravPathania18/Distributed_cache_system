import time
import threading
from typing import Any, Callable, Dict, Optional

from .node import Node
from ._load_state import _LoadState
from ._linked_list_ops import _LinkedListOps
from ._cache_stats import _CacheStats


class LRUCache(_LinkedListOps, _CacheStats):
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

    def __init__(
        self,
        capacity: int,
        on_eviction: Optional[Callable[[str], None]] = None
    ):
        if capacity <= 0:
            raise ValueError("Cache capacity must be greater than 0")

        self.capacity = capacity

        # Optional hook: called with the evicted key whenever the
        # cache removes an entry to stay within capacity
        # (Layer 6.2 - wires LRUCache to CacheStatistics).
        self.on_eviction = on_eviction

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

                    if self.on_eviction is not None:
                        self.on_eviction(lru_node.key)

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
