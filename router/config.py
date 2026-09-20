import os


DEFAULT_NODES = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
]

VIRTUAL_NODES = 100
REPLICATION_FACTOR = 2
HEALTH_INTERVAL = 2
FAILURE_THRESHOLD = 3
TIMEOUT = 2

DEFAULT_DB_PATH = os.environ.get("CACHE_DB_PATH", "data/cache.db")


def nodes_from_env() -> list:
    """Read the cluster node list from the CACHE_NODES env var (optional)."""

    raw = os.environ.get("CACHE_NODES")

    if raw:
        return [
            node.strip()
            for node in raw.split(",")
            if node.strip()
        ]

    return list(DEFAULT_NODES)
