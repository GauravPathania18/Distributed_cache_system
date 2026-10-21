from fastapi import HTTPException

from database.exceptions import DatabaseUnavailableError

from .app import app
from . import _components


@app.get("/db/{key}")
def db_get(key: str):

    try:

        value = _components.database.get(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if value is None:

        raise HTTPException(
            status_code=404,
            detail="Key not found in database"
        )

    return {
        "key": key,
        "value": value,
        "source": "database"
    }


@app.delete("/db/{key}")
def db_delete(key: str):
    """
    Layer 9 - delete the DATABASE row and cascade the cache
    eviction with it (write-invalidate: no request may re-populate
    a stale copy).  Dataset keys additionally re-sync the manifest
    stats and purge that entity's cached search pages.

    The cache-only counterpart of this endpoint is
    DELETE /cache/{key} (evict, row kept).
    """

    try:

        result = _components.cache_service.delete(key)

        dataset = _components.admin.after_key_change(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if not result["deleted"]:

        raise HTTPException(
            status_code=404,
            detail="Key not found"
        )

    return {
        "status": "deleted",
        "key": key,
        "source": "database",
        "nodes": result["nodes"],
        "dataset": dataset or None
    }
