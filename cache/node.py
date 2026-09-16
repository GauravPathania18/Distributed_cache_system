from typing import Any, Optional


class Node:
    """
    Represents one entry in the cache.

    Each node stores:
        - key
        - value
        - expiration timestamp
        - pointers to previous and next nodes

    The prev/next pointers are used by the doubly linked
    list that maintains LRU ordering.
    """

    def __init__(
        self,
        key: str,
        value: Any,
        expires_at: Optional[float] = None
    ):
        self.key = key
        self.value = value
        self.expires_at = expires_at

        self.prev: Optional["Node"] = None
        self.next: Optional["Node"] = None

    def is_expired(self, current_time: float) -> bool:
        """
        Returns True if this cache entry has expired.

        expires_at=None means the entry never expires.
        """

        if self.expires_at is None:
            return False

        return current_time >= self.expires_at

    def __repr__(self) -> str:
        return (
            f"Node("
            f"key={self.key!r}, "
            f"value={self.value!r}, "
            f"expires_at={self.expires_at!r}"
            f")"
        )