import argparse

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Optional

from cache import LRUCache, CacheStatistics


# ---------------------------------------------------------
# CLI ARGUMENTS
# ---------------------------------------------------------

DEFAULT_PORT = 8001
DEFAULT_CAPACITY = 100


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

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port
    )