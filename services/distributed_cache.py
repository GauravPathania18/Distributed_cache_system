import requests


class DistributedCache:
    """
    Data-plane client for the cache cluster.

    Wraps the HTTP calls (with the Layer 4 replication and
    replica-fallback behavior) behind a simple interface:

        get(key)              -> (value, node) or (None, None)
        set(key, value, ttl)  -> list of nodes written
        delete(key)           -> list of nodes invalidated

    It uses a NodeManager only to find which nodes own a key.
    """

    def __init__(self, node_manager, timeout=2):

        self.node_manager = node_manager

        self.timeout = timeout

        self.session = requests.Session()

    # --------------------------------------------------
    # GET (primary -> replica fallback)
    # --------------------------------------------------

    def get(self, key: str):

        for node in self.node_manager.get_nodes(key):

            try:

                response = self.session.get(
                    f"{node}/cache/{key}",
                    timeout=self.timeout
                )

                if response.status_code == 200:

                    return response.json().get("value"), node

                # 404 -> try the next replica
                if response.status_code == 404:

                    continue

            except requests.RequestException:

                continue

        return None, None

    # --------------------------------------------------
    # SET (replicate to primary + replicas)
    # --------------------------------------------------

    def set(self, key: str, value, ttl=None):

        body = {"value": value}

        if ttl is not None:
            body["ttl"] = ttl

        successful_nodes = []

        for node in self.node_manager.get_nodes(key):

            try:

                response = self.session.put(
                    f"{node}/cache/{key}",
                    json=body,
                    timeout=self.timeout
                )

                if response.status_code == 200:

                    successful_nodes.append(node)

            except requests.RequestException:

                continue

        return successful_nodes

    # --------------------------------------------------
    # DELETE (invalidate primary + replicas)
    # --------------------------------------------------

    def delete(self, key: str):

        deleted_from = []

        for node in self.node_manager.get_nodes(key):

            try:

                response = self.session.delete(
                    f"{node}/cache/{key}",
                    timeout=self.timeout
                )

                # 200 = deleted, 404 = already gone -> both fine
                if response.status_code in (200, 404):

                    deleted_from.append(node)

            except requests.RequestException:

                continue

        return deleted_from