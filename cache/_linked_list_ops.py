from typing import Optional

from .node import Node


class _LinkedListOps:

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
