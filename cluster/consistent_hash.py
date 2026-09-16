import hashlib
import bisect


class ConsistentHashRing:

    def __init__(self, virtual_nodes=100):
        self.virtual_nodes = virtual_nodes

        # hash_position -> physical node
        self.ring = {}

        # sorted hash positions
        self.sorted_keys = []

        # physical node -> its virtual-node positions
        self.node_positions = {}

    # ---------------------------------------------------------
    # HASH
    # ---------------------------------------------------------

    def _hash(self, key: str) -> int:

        digest = hashlib.md5(
            key.encode("utf-8")
        ).hexdigest()

        return int(digest, 16)

    # ---------------------------------------------------------
    # ADD NODE
    # ---------------------------------------------------------

    def add_node(self, node: str):

        if node in self.node_positions:
            return

        self.node_positions[node] = []

        for i in range(self.virtual_nodes):

            virtual_node = f"{node}#{i}"

            position = self._hash(virtual_node)

            # Collision protection
            while position in self.ring:
                virtual_node += "_collision"
                position = self._hash(virtual_node)

            self.ring[position] = node
            self.node_positions[node].append(position)

            bisect.insort(self.sorted_keys, position)

    # ---------------------------------------------------------
    # REMOVE NODE
    # ---------------------------------------------------------

    def remove_node(self, node: str):

        if node not in self.node_positions:
            return

        positions = self.node_positions[node]

        for position in positions:

            self.ring.pop(position, None)

            index = bisect.bisect_left(
                self.sorted_keys,
                position
            )

            if (
                index < len(self.sorted_keys)
                and self.sorted_keys[index] == position
            ):
                self.sorted_keys.pop(index)

        del self.node_positions[node]

    # ---------------------------------------------------------
    # GET PRIMARY NODE
    # ---------------------------------------------------------

    def get_node(self, key: str):

        nodes = self.get_nodes(key, 1)

        if not nodes:
            return None

        return nodes[0]

    # ---------------------------------------------------------
    # GET MULTIPLE DISTINCT PHYSICAL NODES
    # ---------------------------------------------------------

    def get_nodes(self, key: str, count: int = 1):
        """
        Return up to `count` distinct physical nodes
        responsible for a key.

        First node  = primary
        Other nodes  = replicas
        """

        if not self.sorted_keys:
            return []

        position = self._hash(key)

        # Find first node clockwise
        index = bisect.bisect_left(
            self.sorted_keys,
            position
        )

        if index == len(self.sorted_keys):
            index = 0

        result = []
        seen = set()

        traversed = 0

        while (
            traversed < len(self.sorted_keys)
            and len(result) < count
        ):

            ring_position = self.sorted_keys[index]

            node = self.ring[ring_position]

            if node not in seen:
                result.append(node)
                seen.add(node)

            index = (index + 1) % len(self.sorted_keys)

            traversed += 1

        return result

    # ---------------------------------------------------------
    # INFO
    # ---------------------------------------------------------

    def get_all_nodes(self):
        """Return all physical nodes currently in the ring."""

        return list(self.node_positions.keys())

    def __len__(self) -> int:
        return len(self.node_positions)
