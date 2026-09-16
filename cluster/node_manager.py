import threading
import time
import requests

from collections import defaultdict

from cluster.consistent_hash import ConsistentHashRing


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
            self._activate_node(node)

    # --------------------------------------------------
    # NODE MANAGEMENT
    # --------------------------------------------------

    def _activate_node(self, node: str):

        with self.lock:

            if node in self.active_nodes:
                return

            self.active_nodes.add(node)

            self.failure_counts[node] = 0

            self.ring.add_node(node)

            print(f"[NODE UP] {node}")

    def _deactivate_node(self, node: str):

        with self.lock:

            if node not in self.active_nodes:
                return

            self.active_nodes.remove(node)

            self.ring.remove_node(node)

            print(f"[NODE DOWN] {node}")

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

    def check_node(self, node: str) -> bool:
        """
        Hit the /health endpoint of a single node.
        Returns True if healthy, False otherwise.
        """

        try:

            response = requests.get(
                f"{node}/health",
                timeout=1
            )

            if response.status_code == 200:
                return True

        except requests.RequestException:
            pass

        return False

    def health_check_loop(self):
        """
        Infinite loop that checks every node periodically.
        Runs in a background daemon thread.
        """

        while True:

            for node in list(self.nodes):

                healthy = self.check_node(node)

                if healthy:

                    # Reset failure counter
                    self.failure_counts[node] = 0

                    # Node recovered -> re-add to ring
                    if node not in self.active_nodes:
                        self._activate_node(node)

                else:

                    self.failure_counts[node] += 1

                    print(
                        f"[HEALTH FAIL] "
                        f"{node} "
                        f"({self.failure_counts[node]}/"
                        f"{self.failure_threshold})"
                    )

                    # Enough consecutive failures -> mark DOWN
                    if (
                        self.failure_counts[node]
                        >= self.failure_threshold
                    ):
                        self._deactivate_node(node)

            time.sleep(self.health_interval)

    def start_health_monitor(self):
        """Start the background health-check thread."""

        thread = threading.Thread(
            target=self.health_check_loop,
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
