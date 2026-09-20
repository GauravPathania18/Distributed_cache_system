import threading

from collections import defaultdict

from cluster.consistent_hash import ConsistentHashRing
from cluster.health_monitor import health_check_loop


class NodeManager:
    """
    Manages cluster membership, health monitoring,
    and the consistent-hash ring.

    Responsibilities:
        - Track active/inactive nodes
        - Run periodic health checks
        - Detect failures (consecutive threshold)
        - Add/remove nodes from the hash ring
        - Provide primary + replica node selection
    """

    def __init__(
        self,
        nodes,
        virtual_nodes=100,
        replication_factor=2,
        health_interval=2,
        failure_threshold=3
    ):

        self.replication_factor = replication_factor

        self.health_interval = health_interval

        self.failure_threshold = failure_threshold

        self.ring = ConsistentHashRing(
            virtual_nodes=virtual_nodes
        )

        # All known nodes (ever registered)
        self.nodes = set(nodes)

        # Currently healthy nodes
        self.active_nodes = set()

        # Consecutive health-check failures per node
        self.failure_counts = defaultdict(int)

        self.lock = threading.RLock()

        # Initially activate all nodes
        for node in nodes:
            from .node_activation import activate_node
            activate_node(self, node)

    # --------------------------------------------------
    # ROUTING
    # --------------------------------------------------

    def get_nodes(self, key: str):
        """
        Return up to replication_factor distinct physical
        nodes for a given key.

        First = primary, rest = replicas.
        """

        with self.lock:

            return self.ring.get_nodes(
                key,
                self.replication_factor
            )

    def get_primary(self, key: str):
        """Return only the primary node for a key."""

        nodes = self.get_nodes(key)

        if not nodes:
            return None

        return nodes[0]

    # --------------------------------------------------
    # HEALTH CHECK
    # --------------------------------------------------

    def start_health_monitor(self):
        """Start the background health-check thread."""

        thread = threading.Thread(
            target=health_check_loop,
            args=(self,),
            daemon=True
        )

        thread.start()

        print("[HEALTH MONITOR] Started")

    # --------------------------------------------------
    # STATUS
    # --------------------------------------------------

    def get_status(self) -> dict:
        """Return a snapshot of the cluster state."""

        with self.lock:

            return {
                "total_nodes": len(self.nodes),
                "active_nodes": sorted(
                    self.active_nodes
                ),
                "inactive_nodes": sorted(
                    self.nodes - self.active_nodes
                ),
                "replication_factor": self.replication_factor,
                "health_interval": self.health_interval,
                "failure_threshold": self.failure_threshold
            }
