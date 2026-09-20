import time
import requests


def check_node(node: str) -> bool:
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


def health_check_loop(manager):
    """
    Infinite loop that checks every node periodically.
    Runs in a background daemon thread.
    """

    from .node_activation import activate_node, deactivate_node

    while True:

        for node in list(manager.nodes):

            healthy = check_node(node)

            if healthy:

                # Reset failure counter
                manager.failure_counts[node] = 0

                # Node recovered -> re-add to ring
                if node not in manager.active_nodes:
                    activate_node(manager, node)

            else:

                manager.failure_counts[node] += 1

                print(
                    f"[HEALTH FAIL] "
                    f"{node} "
                    f"({manager.failure_counts[node]}/"
                    f"{manager.failure_threshold})"
                )

                # Enough consecutive failures -> mark DOWN
                if (
                    manager.failure_counts[node]
                    >= manager.failure_threshold
                ):
                    deactivate_node(manager, node)

        time.sleep(manager.health_interval)
