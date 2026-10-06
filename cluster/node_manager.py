import threading

from collections import defaultdict

from cluster.consistent_hash import ConsistentHashRing
from cluster.health_monitor import health_check_loop
from cluster.node_state import NodeState


class NodeManager:
    """
    Manages cluster membership, health monitoring,
    and the consistent-hash ring.

    Responsibilities:
        - Track every node's lifecycle state
          (STARTING / RECOVERING / READY / UNHEALTHY / FAILED)
        - Run periodic health checks
        - Detect failures (consecutive threshold)
        - Gate recovering nodes out of the hash ring
        - Add/remove nodes from the hash ring
        - Provide primary + replica node selection
    """

    def __init__(
        self,
        nodes,
        virtual_nodes=100,
        replication_factor=2,
        health_interval=2,
        failure_threshold=3,
        initial_state=NodeState.READY,
        recovery_threshold=2
    ):

        nodes = list(nodes)

        self.replication_factor = replication_factor

        self.health_interval = health_interval

        self.failure_threshold = failure_threshold

        # Consecutive healthy checks required before a RECOVERING
        # node is allowed back into the ring.
        self.recovery_threshold = recovery_threshold

        self.ring = ConsistentHashRing(
            virtual_nodes=virtual_nodes
        )

        # All known nodes (ever registered)
        self.nodes = set(nodes)

        # Currently routable nodes (READY + UNHEALTHY)
        self.active_nodes = set()

        # node -> lifecycle state
        self.node_states = {}

        # Consecutive health-check failures per node
        self.failure_counts = defaultdict(int)

        # Consecutive healthy checks while RECOVERING
        self.recovery_counts = defaultdict(int)

        self.lock = threading.RLock()

        from .node_lifecycle import register

        # Bootstrapped nodes join in the configured initial state.
        for node in nodes:
            register(self, node, initial_state)

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
    # LIFECYCLE (Layer 6.1)
    # --------------------------------------------------

    def register_node(self, node: str, initial_state=NodeState.STARTING):
        """
        Add a node at runtime.

        New nodes are gated (STARTING) until the health monitor
        moves them through RECOVERING and into READY.
        """

        from .node_lifecycle import register

        register(self, node, initial_state)

    def get_node_state(self, node: str) -> str:
        """Return the lifecycle state of a single node."""

        with self.lock:

            state = self.node_states.get(node)

            return state.value if state else "unknown"

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

            def state_of(node):
                return self.node_states.get(
                    node,
                    NodeState.FAILED
                )

            return {
                "total_nodes": len(self.nodes),
                "active_nodes": sorted(
                    self.active_nodes
                ),
                "inactive_nodes": sorted(
                    self.nodes - self.active_nodes
                ),
                "node_states": {
                    node: state_of(node).value
                    for node in sorted(self.nodes)
                },
                "recovering_nodes": sorted(
                    node
                    for node in self.nodes
                    if state_of(node) is NodeState.RECOVERING
                ),
                "failed_nodes": sorted(
                    node
                    for node in self.nodes
                    if state_of(node) is NodeState.FAILED
                ),
                "replication_factor": self.replication_factor,
                "health_interval": self.health_interval,
                "failure_threshold": self.failure_threshold,
                "recovery_threshold": self.recovery_threshold
            }
