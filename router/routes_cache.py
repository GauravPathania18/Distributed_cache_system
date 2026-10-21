from fastapi import HTTPException

from database.exceptions import DatabaseUnavailableError

from .app import app
from .models import CacheEntry
from . import _components


@app.get("/cache/{key}")
def get_cache(key: str):

    try:

        result = _components.cache_service.get(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - try again later"
        )

    if result is None:

        raise HTTPException(
            status_code=404,
            detail="Key not found in cache or database"
        )

    return {
        "key": key,
        "value": result["value"],
        "source": result["source"],
        "node": result["node"]
    }


@app.put("/cache/{key}")
def set_cache(
    key: str,
    entry: CacheEntry
):

    try:

        result = _components.cache_service.set(
            key,
            entry.value,
            ttl=entry.ttl
        )

        # Layer 9 - when the key belongs to a dataset, its manifest
        # stats and cached search pages must follow the new row
        # ({} for every non-dataset key - no extra work).
        dataset = _components.admin.after_key_change(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - value was not stored"
        )

    warning = None

    if not result["nodes"]:

        # Layer 9: a scaled-down cluster is a legitimate state, not
        # an error - the database row IS the source of truth and the
        # next read serves it (and repopulates nodes as they return).
        warning = (
            "Stored in the database, but no cache node accepted it "
            "(cluster scaled down or nodes not ready)"
        )

    return {
        "status": "stored",
        "key": key,
        "source": "database+cache" if result["nodes"] else "database",
        "replicated_to": len(result["nodes"]),
        "nodes": result["nodes"],
        "warning": warning,
        "dataset": dataset or None
    }


@app.delete("/cache/{key}")
def delete_cache(key: str):
    """
    Layer 9 - EVICT ONLY: drop the key from every cache node, keep
    the database row (the next read reloads from it).

    The other half of the deletion split lives at
    DELETE /db/{key}: it removes the row and cascades this
    eviction with it.
    """

    result = _components.cache_service.evict(key)

    if not result["evicted"]:

        raise HTTPException(
            status_code=404,
            detail="Key not found in any cache node"
        )

    return {
        "status": "evicted",
        "key": key,
        "database": "kept",
        "nodes": result["nodes"]
    }
