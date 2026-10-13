import os


# 127.0.0.1 instead of localhost: on Windows a "localhost" connect
# may try IPv6 ::1 first and wait for the full connect timeout before
# falling back to IPv4 (~2s per connection). Same machine, no penalty.
DEFAULT_NODES = [
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
    "http://127.0.0.1:8003",
]

VIRTUAL_NODES = 100
REPLICATION_FACTOR = 2
HEALTH_INTERVAL = 2
FAILURE_THRESHOLD = 3
TIMEOUT = 2

# Layer 6.1 - node lifecycle
RECOVERY_THRESHOLD = int(os.environ.get("RECOVERY_THRESHOLD", "2"))
BOOTSTRAP_STATE = os.environ.get("CLUSTER_BOOTSTRAP_STATE", "starting")

# Layer 6.3 - hot keys + cache warm-up
WARMUP_ENABLED = os.environ.get(
    "WARMUP_ENABLED", "on"
).strip().lower() in ("1", "on", "true", "yes")

WARMUP_LIMIT = int(os.environ.get("WARMUP_LIMIT", "1000"))
WARMUP_RATE_LIMIT = float(os.environ.get("WARMUP_RATE_LIMIT", "100"))
HOTKEY_MAX_KEYS = int(os.environ.get("HOTKEY_MAX_KEYS", "10000"))

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
