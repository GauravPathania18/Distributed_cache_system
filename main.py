import argparse
import atexit
import os
import signal
import threading

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional

from cache import LRUCache, CacheStatistics


# ---------------------------------------------------------
# CLI ARGUMENTS
# ---------------------------------------------------------

DEFAULT_PORT = 8001
DEFAULT_CAPACITY = 5000


def parse_args():
    parser = argparse.ArgumentParser(description="Layer 2 - Cache Node")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    return parser.parse_args()


# ---------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------

app = FastAPI(
    title="Distributed Cache Node",
    description="Layer 2 - Cache Node",
    version="1.0"
)

# Layer 6.2 - node-local statistics + hot-key tracking.
# In-memory only: counters are never written to the database.
statistics = CacheStatistics()

# Each cache node process gets its own independent cache.
# Capacity evictions are reported to the statistics object
# through the on_eviction hook.
cache = LRUCache(
    capacity=DEFAULT_CAPACITY,
    on_eviction=statistics.record_eviction
)

# Set in __main__: the uvicorn server this process runs, so the
# shutdown endpoint below can ask it to exit gracefully. None when
# the app is embedded (tests, a parent server).
_server: Optional[uvicorn.Server] = None


# ---------------------------------------------------------
# REQUEST MODEL
# ---------------------------------------------------------

class CacheEntry(BaseModel):

    value: Any

    ttl: Optional[int] = Field(default=None, gt=0)


# ---------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------

@app.get("/health")
def health():

    return {
        "status": "healthy"
    }


# ---------------------------------------------------------
# GET
# ---------------------------------------------------------

@app.get("/cache/{key}")
def get_value(key: str):

    value = cache.get(key)

    if value is None:

        statistics.record_miss(key)

        raise HTTPException(
            status_code=404,
            detail="Cache miss"
        )

    statistics.record_hit(key)

    return {
        "key": key,
        "value": value
    }


# ---------------------------------------------------------
# SET
# ---------------------------------------------------------

@app.put("/cache/{key}")
def set_value(
    key: str,
    entry: CacheEntry
):

    if entry.ttl is None:

        cache.set(
            key,
            entry.value
        )

    else:

        cache.set(
            key,
            entry.value,
            entry.ttl
        )

    statistics.record_set()

    return {
        "status": "stored",
        "key": key
    }


# ---------------------------------------------------------
# DELETE
# ---------------------------------------------------------

@app.delete("/cache/{key}")
def delete_value(key: str):

    statistics.record_delete()

    deleted = cache.delete(key)

    if not deleted:

        raise HTTPException(
            status_code=404,
            detail="Key not found"
        )

    return {
        "status": "deleted",
        "key": key
    }


# ---------------------------------------------------------
# INTERNAL REPLICATION (Layer 7.2/7.3)
#
# The router fans writes out to the primary + replicas through
# these endpoints. They are deliberately separate from the
# client /cache API: a replication write is a local apply with a
# router-assigned sequence and MUST NOT trigger further fan-out.
# Since cache nodes never talk to each other, replication loops
# are structurally impossible - this separation is protocol
# hygiene plus the place version metadata lives.
# ---------------------------------------------------------

class ReplicationEntry(BaseModel):

    value: Any

    ttl: Optional[int] = Field(default=None, gt=0)

    sequence: Optional[int] = Field(default=None, ge=0)


@app.put("/internal/replicate/{key}")
def internal_replicate(key: str, entry: ReplicationEntry):
    """Apply one replicated write locally (no fan-out)."""

    cache.set(
        key,
        entry.value,
        ttl=entry.ttl,
        sequence=entry.sequence
    )

    statistics.record_set()

    return {
        "status": "stored",
        "key": key,
        "sequence": entry.sequence
    }


@app.delete("/internal/replicate/{key}")
def internal_delete(key: str):
    """Apply one replicated delete locally (no fan-out)."""

    statistics.record_delete()

    cache.delete(key)

    # A missing key is not an error here: replication deletes are
    # idempotent and a replica may already be clean.
    return {
        "status": "deleted",
        "key": key
    }


@app.get("/internal/entry/{key}")
def internal_entry(key: str):
    """Versioned read: value + replication sequence."""

    value, sequence = cache.get_entry(key)

    if value is None:

        return {
            "key": key,
            "exists": False,
            "value": None,
            "sequence": None
        }

    return {
        "key": key,
        "exists": True,
        "value": value,
        "sequence": sequence
    }


# ---------------------------------------------------------
# INTERNAL PREFIX FLUSH (Layer 9 - dataset delete / purge)
#
# One call drops every key under a prefix (a whole dataset's
# cached rows, or one entity's cached search results) on THIS
# node. Local apply only - the router fans out per node, nodes
# never talk to each other.
# ---------------------------------------------------------

@app.delete("/internal/prefix/{prefix:path}")
def internal_flush_prefix(prefix: str):

    removed = cache.delete_prefix(prefix)

    for _ in range(removed):
        statistics.record_delete()

    return {
        "status": "flushed",
        "prefix": prefix,
        "removed": removed
    }


# ---------------------------------------------------------
# INTERNAL SHUTDOWN (web UI stop for nodes we do not own)
#
# The router may need to stop a node a different process started
# (started by hand, or by another router). Asking politely beats
# killing: uvicorn finishes
# in-flight requests and runs its lifespan hooks.
# ---------------------------------------------------------

@app.post("/internal/shutdown")
def internal_shutdown():
    """Ask this process's server to exit gracefully."""

    if _server is None:

        raise HTTPException(
            status_code=503,
            detail="Shutdown is only available on a running node process"
        )

    _server.should_exit = True

    return {"status": "shutting down"}


# ---------------------------------------------------------
# EXISTS
# ---------------------------------------------------------

@app.get("/cache/{key}/exists")
def exists(key: str):

    return {
        "key": key,
        "exists": cache.exists(key)
    }


# ---------------------------------------------------------
# STATS
# ---------------------------------------------------------

@app.get("/stats")
def stats():

    return cache.stats()


# ---------------------------------------------------------
# WINDOW STATISTICS (Layer 6.2)
# ---------------------------------------------------------

@app.get("/stats/window")
def stats_window(
    limit: int = Query(default=10, ge=1, le=1000)
):
    """
    Current statistics window: operation counters, hit rate and
    the hottest keys, plus the previous window after each rotation.
    """

    snapshot = statistics.snapshot()

    snapshot["top_keys"] = statistics.top_keys(limit)

    return snapshot


@app.get("/stats/top-keys")
def stats_top_keys(
    limit: int = Query(default=10, ge=1, le=1000)
):
    """Ranking of the most accessed keys in the current window."""

    return {
        "top_keys": statistics.top_keys(limit),
        "tracked_keys": statistics.snapshot()["tracked_keys"],
        "window_seconds": statistics.window_seconds
    }


# ---------------------------------------------------------
# LRU ORDER (Lab 5 - the linked list itself, for the web UI)
# ---------------------------------------------------------

@app.get("/stats/lru")
def stats_lru():
    """
    The recency order every LRU implementation keeps but usually
    never shows: index 0 is the most recently used key and the
    last entry is the node the cache evicts on the next overflow.

    One lock section so `size` and `keys` can never disagree while
    the list is mutating under a concurrent request.
    """

    with cache.lock:

        snapshot = cache.stats()
        snapshot["keys"] = cache.keys()

    return snapshot


# ---------------------------------------------------------
# START SERVER
# ---------------------------------------------------------

if __name__ == "__main__":

    args = parse_args()

    # Rebind the module-level statistics + cache for the requested
    # capacity and start the automatic statistics window rotation.
    statistics = CacheStatistics()
    statistics.start()

    cache = LRUCache(
        capacity=args.capacity,
        on_eviction=statistics.record_eviction
    )

    # PID file: lets a process that did NOT spawn us (the web UI's
    # Cluster control) find and stop this node later. Removed on any
    # clean exit.
    pid_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "pids", f"node-{args.port}.pid"
    )

    os.makedirs(os.path.dirname(pid_path), exist_ok=True)

    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))

    def _remove_pid_file():
        try:
            os.remove(pid_path)
        except OSError:
            pass

    atexit.register(_remove_pid_file)

    # Run uvicorn on a background thread: the main thread keeps the
    # signal handlers and exposes the server object to
    # /internal/shutdown, so another process can stop us gracefully.
    _server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=args.port)
    )

    def _graceful_exit(signum, frame):
        if _server is not None:
            _server.should_exit = True

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    _thread = threading.Thread(target=_server.run, daemon=True)
    _thread.start()
    _thread.join()