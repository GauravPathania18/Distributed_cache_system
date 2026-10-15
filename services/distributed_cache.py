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

    def set(
        self,
        key: str,
        value,
        ttl=None,
        sequence: int = None
    ):
        """
        Fan one write out to the primary + replicas.

        Uses the node's internal replication endpoint so the write
        carries the router-assigned sequence (Layer 7.3) and is
        applied as a local write - nodes never fan out themselves.
        Best-effort: every node that acknowledges is returned; a
        node that rejects or times out is simply skipped.
        """

        body = {"value": value}

        if ttl is not None:
            body["ttl"] = ttl

        if sequence is not None:
            body["sequence"] = sequence

        successful_nodes = []

        for node in self.node_manager.get_nodes(key):

            try:

                response = self.session.put(
                    f"{node}/internal/replicate/{key}",
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

    def delete(self, key: str, sequence: int = None):

        deleted_from = []

        for node in self.node_manager.get_nodes(key):

            try:

                response = self.session.delete(
                    f"{node}/internal/replicate/{key}",
                    timeout=self.timeout
                )

                # 200 = deleted, 404 = already gone -> both fine
                if response.status_code in (200, 404):

                    deleted_from.append(node)

            except requests.RequestException:

                continue

        return deleted_from

    # --------------------------------------------------
    # DIRECT SINGLE-NODE WRITE (Layer 6.3 warm-up / 7.3 sync)
    # --------------------------------------------------

    def write_to_node(
        self,
        node: str,
        key: str,
        value,
        ttl=None,
        sequence: int = None
    ) -> bool:
        """
        Write `key` to exactly `node`, bypassing ring placement.

        A RECOVERING node is gated out of the live ring, so normal
        set() routing would never reach it - and warm-up/sync have
        already decided this key belongs there. Returns True when
        the node acknowledged the write.
        """

        body = {"value": value}

        if ttl is not None:
            body["ttl"] = ttl

        if sequence is not None:
            body["sequence"] = sequence

        try:

            response = self.session.put(
                f"{node}/internal/replicate/{key}",
                json=body,
                timeout=self.timeout
            )

            return response.status_code == 200

        except requests.RequestException:

            return False

    # --------------------------------------------------
    # DIRECT SINGLE-NODE DELETE (Layer 7.3 sync of deletes)
    # --------------------------------------------------

    def delete_from_node(self, node: str, key: str) -> bool:
        """
        Delete `key` from exactly `node` (no ring placement).

        Used by replication-log replay to apply a logged DELETE
        onto a catching-up replica. Missing key = success.
        """

        try:

            response = self.session.delete(
                f"{node}/internal/replicate/{key}",
                timeout=self.timeout
            )

            return response.status_code in (200, 404)

        except requests.RequestException:

            return False