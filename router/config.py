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

# Layer 6.5 - cold-start seeding: what an EMPTY node holds before
# any traffic history exists (warm-up has nothing to reload then).
SEED_ENABLED = os.environ.get(
    "SEED_ENABLED", "on"
).strip().lower() in ("1", "on", "true", "yes")

# popular = top rows by each dataset's detected popularity column
# (score / aggregate_rating / release_year / ...), sampled uniformly
# when a dataset has no such column; random = uniform sample.
# Unknown values are normalized to "popular" by CacheSeeder.
SEED_STRATEGY = os.environ.get("SEED_STRATEGY", "popular").strip().lower()

# Fraction of a node's own capacity seeding may claim - the rest of
# the LRU stays free for the traffic the seeds are meant to attract.
SEED_FILL_RATIO = float(os.environ.get("SEED_FILL_RATIO", "0.6"))

# Candidate rows pulled per dataset (nodes keep only what they own,
# so the pool must be several times a single node's budget).
SEED_PER_DATASET = int(os.environ.get("SEED_PER_DATASET", "250"))

# Capacity assumed for a node that does not answer /stats.
SEED_FALLBACK_CAPACITY = int(os.environ.get("SEED_FALLBACK_CAPACITY", "100"))

# Layer 7.3 - replication log + sync
REPLICATION_ENABLED = os.environ.get(
    "REPLICATION_ENABLED", "on"
).strip().lower() in ("1", "on", "true", "yes")

REPLICATION_LOG_SIZE = int(os.environ.get("REPLICATION_LOG_SIZE", "10000"))
SYNC_SNAPSHOT_THRESHOLD = int(os.environ.get("SYNC_SNAPSHOT_THRESHOLD", "1000"))

# Layer 8 - cached search + web UI
SEARCH_TTL = int(os.environ.get("SEARCH_TTL", "300"))
SEARCH_PAGE_SIZE_DEFAULT = int(os.environ.get("SEARCH_PAGE_SIZE", "20"))
SEARCH_PAGE_SIZE_MAX = int(os.environ.get("SEARCH_PAGE_SIZE_MAX", "100"))

FRONTEND_DIR = os.environ.get(
    "FRONTEND_DIR", os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ), "frontend")
)

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
