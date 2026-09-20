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

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - value was not stored"
        )

    if not result["nodes"]:

        raise HTTPException(
            status_code=503,
            detail="Value stored in database but no cache node accepted it"
        )

    return {
        "status": "stored",
        "key": key,
        "source": "database+cache",
        "replicated_to": len(result["nodes"]),
        "nodes": result["nodes"]
    }


@app.delete("/cache/{key}")
def delete_cache(key: str):

    try:

        result = _components.cache_service.delete(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - could not delete key"
        )

    if not result["deleted"]:

        raise HTTPException(
            status_code=404,
            detail="Key not found"
        )

    return {
        "status": "deleted",
        "key": key,
        "deleted_from_database": result["stored_in_database"],
        "nodes": result["nodes"]
    }
