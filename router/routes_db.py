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

    try:

        deleted = _components.database.delete(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if not deleted:

        raise HTTPException(
            status_code=404,
            detail="Key not found"
        )

    return {
        "status": "deleted",
        "key": key,
        "source": "database"
    }
