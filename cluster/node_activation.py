from .node_lifecycle import enter_state, register
from .node_state import NodeState


def activate_node(manager, node: str):
    """
    Legacy Layer 4 helper: put a node straight into service.

    New code should go through the Layer 6.1 lifecycle
    (node_lifecycle.on_health_result) so that recovering nodes
    are gated before they receive traffic.
    """

    with manager.lock:

        if node not in manager.node_states:
            register(manager, node, NodeState.READY)
            return

        enter_state(manager, node, NodeState.READY, force=True)


def deactivate_node(manager, node: str):
    """
    Legacy Layer 4 helper: take a node out of service (FAILED).

    New code should go through the Layer 6.1 lifecycle.
    """

    with manager.lock:

        if node not in manager.node_states:
            return

        enter_state(manager, node, NodeState.FAILED, force=True)
