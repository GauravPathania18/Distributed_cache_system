def activate_node(manager, node: str):

    with manager.lock:

        if node in manager.active_nodes:
            return

        manager.active_nodes.add(node)

        manager.failure_counts[node] = 0

        manager.ring.add_node(node)

        print(f"[NODE UP] {node}")


def deactivate_node(manager, node: str):

    with manager.lock:

        if node not in manager.active_nodes:
            return

        manager.active_nodes.remove(node)

        manager.ring.remove_node(node)

        print(f"[NODE DOWN] {node}")
