from enum import Enum


class NodeState(str, Enum):
    """
    Layer 6.1 - Node lifecycle states.

        STARTING -> RECOVERING -> READY
                                     |
                                 UNHEALTHY
                                     |
                                   FAILED -> RECOVERING (gated rejoin)
                                   FAILED -> STARTING (explicit restart)

    Only routable states (READY + UNHEALTHY) sit in the
    consistent-hash ring and receive normal traffic. Everything
    else is invisible to routing, which is what protects the
    database from a cold node being slammed.
    """

    STARTING = "starting"
    RECOVERING = "recovering"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    FAILED = "failed"


# Which transitions the state machine is allowed to make.
# Anything not listed here raises InvalidTransitionError.
ALLOWED_TRANSITIONS = {
    NodeState.    STARTING: {
        NodeState.STARTING,   # health check failed, under threshold
        NodeState.RECOVERING, # first healthy check
        NodeState.FAILED,     # never came up (failure_threshold)
    },
    NodeState.RECOVERING: {
        NodeState.RECOVERING, # healthy but not enough checks yet
        NodeState.READY,      # recovery_threshold reached
        NodeState.FAILED,     # lost again while recovering
    },
    NodeState.READY: {
        NodeState.READY,      # healthy, nothing to do
        NodeState.UNHEALTHY,  # failures below the threshold
        NodeState.FAILED,     # failures reached the threshold
    },
    NodeState.UNHEALTHY: {
        NodeState.UNHEALTHY,  # still failing, still under threshold
        NodeState.READY,      # recovered
        NodeState.FAILED,     # failures reached the threshold
    },
    NodeState.FAILED: {
        NodeState.FAILED,     # still down
        NodeState.RECOVERING, # healthy again -> gated rejoin
        NodeState.STARTING,   # explicit restart (start_node) -> re-gate
    },
}


class InvalidTransitionError(Exception):
    """Raised when the state machine is asked to make an illegal jump."""

    def __init__(self, node, current, target):

        self.node = node
        self.current = current
        self.target = target

        super().__init__(
            f"invalid node transition for {node}: "
            f"{current.value} -> {target.value}"
        )


def can_transition(current, target) -> bool:
    """True if `current -> target` is a legal transition."""

    return target in ALLOWED_TRANSITIONS.get(current, set())
