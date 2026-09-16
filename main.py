import argparse

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Optional

from cache import LRUCache


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

# Each cache node process gets its own independent cache.
cache = LRUCache(capacity=DEFAULT_CAPACITY)


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

        raise HTTPException(
            status_code=404,
            detail="Cache miss"
        )

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

    return {
        "status": "stored",
        "key": key
    }


# ---------------------------------------------------------
# DELETE
# ---------------------------------------------------------

@app.delete("/cache/{key}")
def delete_value(key: str):

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
# START SERVER
# ---------------------------------------------------------

if __name__ == "__main__":

    args = parse_args()

    # Rebind the module-level cache for the requested capacity.
    cache = LRUCache(capacity=args.capacity)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port
    )