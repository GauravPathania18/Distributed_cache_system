from .events import LifecycleEvent, fire_listeners
from .node_lifecycle import enter_state, register
from .node_state import NodeState


def activate_node(manager, node: str):
    """
    Legacy Layer 4 helper: put a node straight into service.

    New code should go through the Layer 6.1 lifecycle
    (node_lifecycle.on_health_result) so that recovering nodes
    are gated before they receive traffic.
    """

    event = None

    with manager.lock:

        if node not in manager.node_states:
            register(manager, node, NodeState.READY)

        else:

            before = manager.node_states[node]

            after = enter_state(
                manager, node, NodeState.READY, force=True
            )

            if after is not before:
                event = LifecycleEvent(node, before, after)

    if event is not None:
        fire_listeners(manager, event)


def deactivate_node(manager, node: str):
    """
    Legacy Layer 4 helper: take a node out of service (FAILED).

    New code should go through the Layer 6.1 lifecycle.
    """

    event = None

    with manager.lock:

        if node not in manager.node_states:
            return

        before = manager.node_states[node]

        after = enter_state(
            manager, node, NodeState.FAILED, force=True
        )

        if after is not before:
            event = LifecycleEvent(node, before, after)

    if event is not None:
        fire_listeners(manager, event)
