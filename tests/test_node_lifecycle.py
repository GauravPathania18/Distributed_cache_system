import threading

import pytest

from cluster.health_monitor import run_health_pass
from cluster.node_lifecycle import (
    enter_state,
    on_health_result,
    register,
)
from cluster.node_manager import NodeManager
from cluster.node_state import InvalidTransitionError, NodeState


NODE1 = "http://localhost:8001"
NODE2 = "http://localhost:8002"
NODE3 = "http://localhost:8003"
NODE4 = "http://localhost:8004"
NODE5 = "http://localhost:8005"
NODE6 = "http://localhost:8006"


def make_manager(
    nodes=None,
    initial_state=NodeState.READY,
    failure_threshold=3,
    recovery_threshold=2
):
    return NodeManager(
        nodes=list(nodes or []),
        virtual_nodes=10,
        failure_threshold=failure_threshold,
        recovery_threshold=recovery_threshold,
        initial_state=initial_state
    )


def healthy(manager, node, times=1):
    for _ in range(times):
        on_health_result(manager, node, True)


def unhealthy(manager, node, times=1):
    for _ in range(times):
        on_health_result(manager, node, False)


# =========================================================
# BOOTSTRAP
# =========================================================

def test_bootstrap_ready_nodes_are_routable():
    """Default bootstrap: declared nodes are READY and in the ring."""

    manager = make_manager([NODE1, NODE2])

    assert manager.get_node_state(NODE1) == "ready"
    assert NODE1 in manager.active_nodes
    assert NODE1 in manager.ring.get_all_nodes()


def test_bootstrap_starting_nodes_are_gated():
    """Gated bootstrap: declared nodes are STARTING and not routable."""

    manager = make_manager([NODE1], initial_state=NodeState.STARTING)

    assert manager.get_node_state(NODE1) == "starting"
    assert NODE1 not in manager.active_nodes
    assert NODE1 not in manager.ring.get_all_nodes()


def test_register_node_defaults_to_starting():
    """Runtime registration gates the node until it proves itself."""

    manager = make_manager([NODE1])

    manager.register_node(NODE2)

    assert manager.get_node_state(NODE2) == "starting"
    assert NODE2 not in manager.active_nodes
    assert NODE2 not in manager.ring.get_all_nodes()


def test_get_node_state_for_unknown_node():
    manager = make_manager()

    assert manager.get_node_state(NODE1) == "unknown"


# =========================================================
# STARTING -> RECOVERING -> READY
# =========================================================

def test_starting_node_becomes_recovering_on_first_healthy_check():
    manager = make_manager(initial_state=NodeState.STARTING)

    register(manager, NODE1, NodeState.STARTING)
    healthy(manager, NODE1)

    assert manager.get_node_state(NODE1) == "recovering"
    assert NODE1 not in manager.active_nodes


def test_recovering_node_needs_recovery_threshold_checks():
    """RECOVERING -> READY only after recovery_threshold healthy checks."""

    manager = make_manager(
        initial_state=NodeState.STARTING,
        recovery_threshold=2
    )

    register(manager, NODE1, NodeState.STARTING)

    healthy(manager, NODE1)                 # STARTING -> RECOVERING
    assert manager.get_node_state(NODE1) == "recovering"

    healthy(manager, NODE1)                 # recovery_count = 1
    assert manager.get_node_state(NODE1) == "recovering"
    assert NODE1 not in manager.active_nodes

    healthy(manager, NODE1)                 # recovery_count = 2 -> READY
    assert manager.get_node_state(NODE1) == "ready"
    assert NODE1 in manager.active_nodes
    assert NODE1 in manager.ring.get_all_nodes()


def test_starting_node_that_never_comes_up_becomes_failed():
    """failure_threshold failures move a STARTING node to FAILED."""

    manager = make_manager(
        initial_state=NodeState.STARTING,
        failure_threshold=3
    )

    register(manager, NODE1, NodeState.STARTING)

    unhealthy(manager, NODE1, times=3)

    assert manager.get_node_state(NODE1) == "failed"
    assert NODE1 not in manager.active_nodes


# =========================================================
# FAILURE DETECTION (READY -> UNHEALTHY -> FAILED)
# =========================================================

def test_unhealthy_stays_routable_below_threshold():
    """A transient blip must not evict the node from the ring."""

    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=2)

    assert manager.get_node_state(NODE1) == "unhealthy"
    assert NODE1 in manager.active_nodes
    assert NODE1 in manager.ring.get_all_nodes()


def test_ready_node_becomes_failed_at_threshold():
    """failure_threshold consecutive failures -> FAILED + removed."""

    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=3)

    assert manager.get_node_state(NODE1) == "failed"
    assert NODE1 not in manager.active_nodes
    assert NODE1 not in manager.ring.get_all_nodes()


def test_unhealthy_node_recovers_to_ready():
    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=2)
    assert manager.get_node_state(NODE1) == "unhealthy"

    healthy(manager, NODE1)

    assert manager.get_node_state(NODE1) == "ready"
    assert NODE1 in manager.active_nodes


def test_counters_reset_when_node_becomes_ready():
    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=3)       # -> FAILED
    healthy(manager, NODE1)                  # -> RECOVERING
    healthy(manager, NODE1)
    healthy(manager, NODE1)                  # -> READY

    assert manager.failure_counts[NODE1] == 0
    assert manager.recovery_counts[NODE1] == 0


# =========================================================
# GATED REJOIN (sections 14-16)
# =========================================================

def test_failed_node_is_gated_on_rejoin():
    """A restarted node must not jump straight back into the ring."""

    manager = make_manager([NODE1], recovery_threshold=2)

    unhealthy(manager, NODE1, times=3)       # -> FAILED
    assert manager.get_node_state(NODE1) == "failed"

    healthy(manager, NODE1)                  # -> RECOVERING (gated)
    assert manager.get_node_state(NODE1) == "recovering"
    assert NODE1 not in manager.active_nodes
    assert NODE1 not in manager.ring.get_all_nodes()

    healthy(manager, NODE1)                  # recovery_count = 1
    assert manager.get_node_state(NODE1) == "recovering"

    healthy(manager, NODE1)                  # recovery_count = 2 -> READY
    assert manager.get_node_state(NODE1) == "ready"
    assert NODE1 in manager.active_nodes
    assert NODE1 in manager.ring.get_all_nodes()


def test_failure_during_recovery_resets_recovery_count():
    """A node that flaps while recovering starts its recovery over."""

    manager = make_manager(
        initial_state=NodeState.STARTING,
        failure_threshold=3,
        recovery_threshold=2
    )

    register(manager, NODE1, NodeState.STARTING)

    healthy(manager, NODE1)                  # -> RECOVERING
    healthy(manager, NODE1)                  # recovery_count = 1
    assert manager.recovery_counts[NODE1] == 1

    unhealthy(manager, NODE1)                # count reset, still gated
    assert manager.get_node_state(NODE1) == "recovering"
    assert manager.recovery_counts[NODE1] == 0

    healthy(manager, NODE1)                  # recovery_count = 1
    assert manager.get_node_state(NODE1) == "recovering"

    healthy(manager, NODE1)                  # recovery_count = 2 -> READY
    assert manager.get_node_state(NODE1) == "ready"


# =========================================================
# STATE MACHINE RULES
# =========================================================

def test_invalid_transition_raises():
    manager = make_manager()

    register(manager, NODE1, NodeState.FAILED)

    with pytest.raises(InvalidTransitionError) as excinfo:
        enter_state(manager, NODE1, NodeState.READY)

    assert excinfo.value.node == NODE1
    assert excinfo.value.current is NodeState.FAILED
    assert excinfo.value.target is NodeState.READY

    # The node must not have moved.
    assert manager.get_node_state(NODE1) == "failed"


def test_healthy_result_for_unknown_node_is_ignored():
    manager = make_manager()

    on_health_result(manager, NODE1, True)
    on_health_result(manager, NODE1, False)

    assert NODE1 not in manager.node_states


# =========================================================
# HEALTH PASS + STATUS
# =========================================================

def test_run_health_pass_uses_injected_checker():
    manager = make_manager([NODE1, NODE2])
    manager.register_node(NODE3, NodeState.STARTING)

    results = {
        NODE1: False,
        NODE2: True,
        NODE3: True,
    }

    run_health_pass(manager, checker=lambda node: results[node])

    assert manager.get_node_state(NODE1) == "unhealthy"
    assert manager.get_node_state(NODE2) == "ready"
    assert manager.get_node_state(NODE3) == "recovering"


def test_status_reports_lifecycle_fields():
    manager = make_manager([NODE1, NODE2], failure_threshold=3)
    manager.register_node(NODE3, NodeState.STARTING)

    unhealthy(manager, NODE1, times=3)       # -> FAILED

    status = manager.get_status()

    assert status["total_nodes"] == 3
    assert status["node_states"] == {
        NODE1: "failed",
        NODE2: "ready",
        NODE3: "starting",
    }
    assert status["failed_nodes"] == [NODE1]
    assert status["recovering_nodes"] == []
    assert status["active_nodes"] == [NODE2]
    assert status["inactive_nodes"] == [NODE1, NODE3]
    assert status["recovery_threshold"] == 2
    assert status["failure_threshold"] == 3

    healthy(manager, NODE1)                  # -> RECOVERING

    status = manager.get_status()
    assert status["recovering_nodes"] == [NODE1]
    assert status["failed_nodes"] == []
    assert NODE1 not in status["active_nodes"]


# =========================================================
# CONCURRENCY
# =========================================================

def test_concurrent_health_results_stay_consistent():
    manager = make_manager([NODE1, NODE2], failure_threshold=3)

    def hammer(node, healthy_value):
        for _ in range(100):
            on_health_result(manager, node, healthy_value)

    threads = [
        threading.Thread(target=hammer, args=(NODE1, True)),
        threading.Thread(target=hammer, args=(NODE1, False)),
        threading.Thread(target=hammer, args=(NODE2, False)),
        threading.Thread(target=hammer, args=(NODE2, True)),
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    for node in (NODE1, NODE2):

        state = manager.node_states[node]

        assert state in set(NodeState)

        # Ring membership must always match the state.
        if state in (NodeState.READY, NodeState.UNHEALTHY):
            assert node in manager.active_nodes
            assert node in manager.ring.get_all_nodes()
        else:
            assert node not in manager.active_nodes
            assert node not in manager.ring.get_all_nodes()


# =========================================================
# PUBLIC API (start_node / mark_* / is_available)
# =========================================================

def test_start_node_registers_unknown_node_gated():
    manager = make_manager([NODE1])

    result = manager.start_node(NODE2)

    assert result == "starting"
    assert manager.get_node_state(NODE2) == "starting"
    assert not manager.is_available(NODE2)
    assert NODE2 not in manager.ring.get_all_nodes()


def test_start_node_restarts_failed_node():
    """FAILED -> STARTING: explicit restart re-gates the node."""

    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=3)       # -> FAILED
    assert manager.get_node_state(NODE1) == "failed"

    result = manager.start_node(NODE1)

    assert result == "starting"
    assert manager.get_node_state(NODE1) == "starting"
    assert NODE1 not in manager.active_nodes
    assert NODE1 not in manager.ring.get_all_nodes()

    # Stale failure counts are cleared so the node gets a fresh
    # failure_threshold budget on its first health check.
    assert manager.failure_counts[NODE1] == 0


def test_start_node_is_idempotent_for_running_nodes():
    manager = make_manager([NODE1])          # READY

    assert manager.start_node(NODE1) == "ready"
    assert manager.is_available(NODE1)
    assert NODE1 in manager.ring.get_all_nodes()


def test_mark_recovering_gates_the_node():
    manager = make_manager([NODE1])
    manager.start_node(NODE2)

    assert manager.mark_recovering(NODE2) == "recovering"
    assert not manager.is_available(NODE2)
    assert NODE2 not in manager.ring.get_all_nodes()


def test_mark_recovering_from_ready_is_invalid():
    """A serving node cannot be yanked into RECOVERING directly."""

    manager = make_manager([NODE1])

    with pytest.raises(InvalidTransitionError):
        manager.mark_recovering(NODE1)

    assert manager.get_node_state(NODE1) == "ready"


def test_mark_ready_is_blocked_from_failed():
    """The rejoin gate: FAILED cannot jump straight to READY."""

    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=3)       # -> FAILED

    with pytest.raises(InvalidTransitionError):
        manager.mark_ready(NODE1)

    assert manager.get_node_state(NODE1) == "failed"
    assert not manager.is_available(NODE1)


def test_mark_ready_from_recovering_puts_node_in_service():
    manager = make_manager([NODE1], failure_threshold=3)

    unhealthy(manager, NODE1, times=3)       # -> FAILED
    manager.start_node(NODE1)                # FAILED -> STARTING
    manager.mark_recovering(NODE1)           # STARTING -> RECOVERING

    assert manager.mark_ready(NODE1) == "ready"
    assert manager.is_available(NODE1)
    assert NODE1 in manager.ring.get_all_nodes()
    assert manager.failure_counts[NODE1] == 0
    assert manager.recovery_counts[NODE1] == 0


def test_mark_failed_evicts_node_from_ring():
    manager = make_manager([NODE1])
    assert manager.is_available(NODE1)

    assert manager.mark_failed(NODE1) == "failed"

    assert not manager.is_available(NODE1)
    assert NODE1 not in manager.active_nodes
    assert NODE1 not in manager.ring.get_all_nodes()


def test_is_available_truth_table():
    manager = make_manager(
        [NODE1, NODE2, NODE5],
        failure_threshold=3
    )

    manager.register_node(NODE3, NodeState.STARTING)
    manager.register_node(NODE4, NodeState.STARTING)

    manager.mark_recovering(NODE3)           # recovering
    unhealthy(manager, NODE4, times=3)       # failed
    unhealthy(manager, NODE2)                # unhealthy (blip)

    availability = {
        NODE1: True,    # ready
        NODE2: True,    # unhealthy -> still routable
        NODE3: False,   # recovering -> gated
        NODE4: False,   # failed -> evicted
        NODE5: True,    # ready (untouched)
        NODE6: False,   # unknown
    }

    for node, expected in availability.items():
        assert manager.is_available(node) is expected, node


def test_mark_on_unknown_node_raises():
    manager = make_manager()

    for mark in (
        manager.mark_recovering,
        manager.mark_ready,
        manager.mark_failed
    ):
        with pytest.raises(ValueError):
            mark(NODE1)
