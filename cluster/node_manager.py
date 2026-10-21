import threading

from collections import defaultdict

from cluster.consistent_hash import ConsistentHashRing
from cluster.events import LifecycleEvent, fire_listeners
from cluster.health_monitor import health_check_loop
from cluster.node_state import NodeState


class NodeManager:
    """
    Manages cluster membership, health monitoring,
    and the consistent-hash ring.

    Responsibilities:
        - Track every node's lifecycle state
          (STARTING / RECOVERING / READY / UNHEALTHY / FAILED)
        - Expose the Layer 6.1 public lifecycle API
          (start_node / mark_recovering / mark_ready /
          mark_failed / is_available)
        - Run periodic health checks
        - Detect failures (consecutive threshold)
        - Gate recovering nodes out of the hash ring
        - Add/remove nodes from the hash ring
        - Provide primary + replica node selection
        - Fire lifecycle events after the lock is released
          (Layer 6.3: warm-up trigger, later failover hooks)
        - Hold the optional recovery gate that a RECOVERING
          node must satisfy before it may become READY
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

        # LifecycleEvent callbacks (fired after the lock is released)
        self.listeners = []

        # callback(manager) run after every health pass (post-lock)
        self.health_hooks = []

        # Optional extra condition(node) -> bool gating
        # RECOVERING -> READY (None = ungated).
        self.recovery_gate = None

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

    def ring_view(self, node: str = None):
        """
        Snapshot of the ring, optionally including `node` even when
        that node is gated out of the live ring (Layer 6.3).

        Build it once per warm-up, then answer many ownership
        questions against the snapshot - the live ring keeps
        mutating underneath (health monitor) and must not be read
        without the lock.
        """

        with self.lock:

            if node is None:
                return self.ring.copy()

            return self.ring.with_node(node)

    def declared_ring_view(self):
        """
        Snapshot of the ring over EVERY declared node, gated or not.

        ring_view(node) answers "where will traffic route once THIS
        node rejoins the live ring" - but during a full cold start
        every node is gated, so that view has a single member and
        each node would own (and therefore load) the identical
        whole keyspace. Layer 6.5 seeding asks the different
        question "which slice will this node own once the CLUSTER
        is recovered": the declared membership. Each node then seeds
        a distinct slice, so the cluster's unique coverage is
        N x the per-node budget instead of 1 x it.

        Nodes are only ever ADDED (register_node), never removed,
        so the declared set cannot contain phantoms.
        """

        with self.lock:

            view = ConsistentHashRing(
                virtual_nodes=self.ring.virtual_nodes
            )

            for node in self.nodes:
                view.add_node(node)

            return view

    def expected_primary(self, key: str, node: str):
        """
        Who WOULD be the primary for `key` once `node` rejoins
        (Layer 6.3 ownership check, Section 10 corrected).

        The live ring can never return a gated node, so ownership
        is computed on a view with `node` included.
        """

        view = self.ring_view(node)

        nodes = view.get_nodes(key, 1)

        return nodes[0] if nodes else None

    # --------------------------------------------------
    # LISTENERS + RECOVERY GATE (Layer 6.3)
    # --------------------------------------------------

    def add_listener(self, callback):
        """
        Register for lifecycle events. Callbacks receive a
        LifecycleEvent and run AFTER the lock is released, so they
        may safely do HTTP/DB work.
        """

        with self.lock:
            self.listeners.append(callback)

    def remove_listener(self, callback):

        with self.lock:

            if callback in self.listeners:
                self.listeners.remove(callback)

    def add_health_hook(self, callback):
        """
        Register callback(manager): runs after every health pass,
        outside the lock (Layer 6.3 warm-up retries).
        """

        with self.lock:
            self.health_hooks.append(callback)

    def set_recovery_gate(self, gate):
        """
        Set the extra RECOVERING -> READY condition.

        `gate(node) -> bool` must return True before a RECOVERING
        node is allowed into the ring (Layer 6.3: warm-up done).
        None removes the gate. The explicit mark_ready() admin API
        bypasses it - it is a human override.
        """

        with self.lock:
            self.recovery_gate = gate

    # --------------------------------------------------
    # LIFECYCLE (Layer 6.1)
    # --------------------------------------------------

    def start_node(self, node: str) -> str:
        """
        Bring a node online (gated).

            unknown node -> registered as STARTING (not routable yet)
            FAILED node  -> explicit restart: FAILED -> STARTING,
                            the node must re-prove itself through
                            RECOVERING before it gets traffic
            any other    -> no-op; the lifecycle is already running

        Public API over the Layer 6.1 state machine.
        Returns the node's state value.
        """

        from .node_lifecycle import enter_state, register

        event = None

        with self.lock:

            current = self.node_states.get(node)

            if current is None:
                register(self, node, NodeState.STARTING)

            elif current is NodeState.FAILED:
                after = enter_state(self, node, NodeState.STARTING)

                if after is not current:
                    event = LifecycleEvent(node, current, after)

            result = self.node_states[node].value

        if event is not None:
            fire_listeners(self, event)

        return result

    def mark_recovering(self, node: str) -> str:
        """
        Gate a node: RECOVERING, not in the hash ring.

        Legal from STARTING (first healthy check) and FAILED
        (gated rejoin). Illegal jumps raise InvalidTransitionError.
        """

        return self._mark(node, NodeState.RECOVERING)

    def mark_ready(self, node: str) -> str:
        """
        Put a node into service: READY, in the hash ring.

        Legal from RECOVERING (recovery_threshold reached) and
        UNHEALTHY (blip over). From FAILED it raises
        InvalidTransitionError - rejoin must go through RECOVERING.
        """

        return self._mark(node, NodeState.READY)

    def mark_failed(self, node: str) -> str:
        """
        Take a node out of service: FAILED, evicted from the ring.

        Legal from every other state.
        """

        return self._mark(node, NodeState.FAILED)

    def is_available(self, node: str) -> bool:
        """
        True if the node currently receives traffic.

        Routable states are READY and UNHEALTHY (a transient blip
        keeps the node in the ring). STARTING, RECOVERING, FAILED
        and unknown nodes are not available.
        """

        with self.lock:
            return node in self.active_nodes

    def _mark(self, node: str, target: NodeState) -> str:
        """Validated transition helper for the named lifecycle ops."""

        from .node_lifecycle import enter_state

        event = None

        with self.lock:

            if node not in self.node_states:
                raise ValueError(f"unknown node: {node}")

            current = self.node_states[node]

            after = enter_state(self, node, target)

            if after is not current:
                event = LifecycleEvent(node, current, after)

            result = after.value

        if event is not None:
            fire_listeners(self, event)

        return result

    def register_node(self, node: str, initial_state=NodeState.STARTING):
        """
        Add a node at runtime.

        Layer 4 name for the same operation as start_node():
        new nodes are gated (STARTING) until the health monitor
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
