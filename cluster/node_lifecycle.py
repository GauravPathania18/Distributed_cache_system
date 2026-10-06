from .node_state import (
    NodeState,
    InvalidTransitionError,
    can_transition,
)

# States whose nodes are visible to the consistent-hash ring.
ROUTABLE_STATES = {
    NodeState.READY,
    NodeState.UNHEALTHY,
}


# ---------------------------------------------------------
# STATE ENTRY (side effects + logging)
# ---------------------------------------------------------

def enter_state(manager, node: str, target: NodeState, force=False):
    """
    Move a node to `target`, applying the side effects:

        - ROUTABLE states join the hash ring / active set
        - gated & failed states leave the hash ring
        - READY clears all failure + recovery counters

    Raises InvalidTransitionError for illegal jumps unless
    `force` is set (legacy wrappers only).
    """

    with manager.lock:

        current = manager.node_states.get(node, NodeState.STARTING)

        # Self-loops are no-ops; callers own the counters.
        if current == target:
            return current

        if not force and not can_transition(current, target):
            raise InvalidTransitionError(node, current, target)

        manager.node_states[node] = target

        if target in ROUTABLE_STATES:
            manager.active_nodes.add(node)
            manager.ring.add_node(node)
        else:
            manager.active_nodes.discard(node)
            manager.ring.remove_node(node)

        if target is NodeState.READY:
            manager.failure_counts[node] = 0
            manager.recovery_counts[node] = 0

        elif target is NodeState.RECOVERING:
            manager.recovery_counts[node] = 0

        print(f"[NODE {target.value.upper()}] {node}")

        return target


# ---------------------------------------------------------
# REGISTRATION
# ---------------------------------------------------------

def register(manager, node: str, initial_state=NodeState.STARTING):
    """
    Add a node to the manager in a given lifecycle state.

    Bootstrapped nodes (declared in the router config) register as
    READY so the cluster serves traffic immediately. Dynamically
    registered nodes register as STARTING and are gated until they
    prove themselves through the health monitor.
    """

    initial_state = NodeState(initial_state)

    with manager.lock:

        manager.nodes.add(node)

        if node in manager.node_states:
            return

        manager.node_states[node] = initial_state

        if initial_state in ROUTABLE_STATES:
            manager.active_nodes.add(node)
            manager.ring.add_node(node)

        print(f"[NODE {initial_state.value.upper()}] {node}")


# ---------------------------------------------------------
# HEALTH RESULT (the state machine driver)
# ---------------------------------------------------------

def on_health_result(manager, node: str, healthy: bool):
    """
    Feed one health-check outcome into the lifecycle.

        healthy:
            STARTING   -> RECOVERING
            RECOVERING -> count++; READY once recovery_threshold hit
            UNHEALTHY  -> READY
            FAILED     -> RECOVERING (gated rejoin)
            READY      -> stay

        unhealthy:
            failure counter increments; once failure_threshold
            consecutive failures are seen -> FAILED (leave the ring).
            Otherwise READY -> UNHEALTHY, and gated states stay gated.
    """

    with manager.lock:

        current = manager.node_states.get(node)

        if current is None:
            return

        if healthy:

            manager.failure_counts[node] = 0

            if current is NodeState.STARTING:
                enter_state(manager, node, NodeState.RECOVERING)

            elif current is NodeState.RECOVERING:
                manager.recovery_counts[node] += 1

                if (
                    manager.recovery_counts[node]
                    >= manager.recovery_threshold
                ):
                    enter_state(manager, node, NodeState.READY)

            elif current is NodeState.UNHEALTHY:
                enter_state(manager, node, NodeState.READY)

            elif current is NodeState.FAILED:
                enter_state(manager, node, NodeState.RECOVERING)

            # READY: already in service, nothing to do.

        else:

            manager.failure_counts[node] += 1
            manager.recovery_counts[node] = 0

            failures = manager.failure_counts[node]

            print(
                f"[HEALTH FAIL] "
                f"{node} "
                f"({failures}/{manager.failure_threshold})"
            )

            if failures >= manager.failure_threshold:
                enter_state(manager, node, NodeState.FAILED)

            elif current is NodeState.READY:
                enter_state(manager, node, NodeState.UNHEALTHY)

            # STARTING / RECOVERING / UNHEALTHY / FAILED:
            # keep waiting for the threshold before changing state.
