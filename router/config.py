import os

from urllib.parse import urlparse


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


def normalize_node(entry: str):
    """
    One node entry -> one usable URL.

    Entries may be full URLs or bare ports. localhost / [::1] are
    rewritten to 127.0.0.1: a "localhost" connect can try IPv6 first
    and burn the whole connect timeout before falling back to IPv4
    (~2s per request on Windows). Returns None for blank entries.
    """

    entry = entry.strip()

    if not entry:
        return None

    if entry.startswith("http://") or entry.startswith("https://"):
        entry = entry.rstrip("/")
    else:
        entry = f"http://127.0.0.1:{entry}"

    parsed = urlparse(entry)

    if (parsed.hostname or "").lower() in ("localhost", "::1"):
        netloc = "127.0.0.1"
        if parsed.port:
            netloc += f":{parsed.port}"
        entry = f"{parsed.scheme}://{netloc}"

    return entry


def nodes_from_env() -> list:
    """Read the cluster node list from CACHE_NODES (URLs or bare ports)."""

    raw = os.environ.get("CACHE_NODES")

    entries = raw.split(",") if raw else list(DEFAULT_NODES)

    nodes = [normalize_node(entry) for entry in entries]

    return [node for node in nodes if node]
