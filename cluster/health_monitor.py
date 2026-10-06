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


def run_health_pass(manager, checker=check_node):
    """
    Check every known node exactly once and feed the results
    into the Layer 6.1 lifecycle state machine.
    """

    from .node_lifecycle import on_health_result

    for node in list(manager.nodes):

        healthy = checker(node)

        on_health_result(manager, node, healthy)


def health_check_loop(manager, checker=check_node):
    """
    Infinite loop that checks every node periodically.
    Runs in a background daemon thread.
    """

    while True:

        run_health_pass(manager, checker)

        time.sleep(manager.health_interval)
