"""
Layer 9 - dataset management from the web UI.

    GET    /datasets                         list w/ live row counts
    POST   /datasets                         create an isolated dataset
    GET    /datasets/{name}                  one dataset
    DELETE /datasets/{name}                  remove it everywhere
    GET    /datasets/{name}/entries          browse rows (paged)
    POST   /datasets/{name}/entries          add/upsert one entry
    DELETE /datasets/{name}/entries?key=...  delete one entry

All mutation logic lives in services/dataset_admin.py; this module
only translates exceptions into HTTP (400 bad input, 404 unknown
dataset/key, 503 database unavailable).
"""

from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from database.exceptions import DatabaseUnavailableError

from .app import app
from . import _components


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    key_column: Optional[str] = Field(default=None, max_length=64)


class EntryCreate(BaseModel):
    value: dict


def _admin():
    return _components.admin


@app.get("/datasets")
def list_datasets():
    """Every registered dataset with prefix, schema and live rows."""

    try:
        return {"datasets": _admin().list()}
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/datasets")
def create_dataset(body: DatasetCreate):
    """New isolated dataset: own SQLite file, own prefix, own entity."""

    try:
        dataset = _admin().create(body.name, key_column=body.key_column)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return {"status": "created", "dataset": dataset}


@app.get("/datasets/{name}")
def get_dataset(name: str):

    try:
        return {"dataset": _admin().get(name)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown dataset: {name}")
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.delete("/datasets/{name}")
def delete_dataset(name: str):
    """
    Remove a dataset everywhere: cached rows flushed from every
    node, search caches purged, replication log trimmed, manifest
    entry and SQLite file deleted.
    """

    try:
        result = _admin().delete_dataset(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown dataset: {name}")
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return result


@app.get("/datasets/{name}/entries")
def list_entries(
    name: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Paged key/value rows of one dataset (sorted by key)."""

    try:
        return _admin().entries(name, limit=limit, offset=offset)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown dataset: {name}")
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/datasets/{name}/entries")
def add_entry(name: str, body: EntryCreate):
    """
    Add one entry - conflict-free: the key is derived server-side
    (declared key column, then "id", then the next free number) and
    the write goes through the normal write-through path (DB row +
    fan-out to primary + replicas). Re-posting an existing id
    upserts it (`created: false`).
    """

    try:
        result = _admin().add_entry(name, body.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown dataset: {name}")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return {"status": "stored", **result}


@app.delete("/datasets/{name}/entries")
def delete_entry(name: str, key: str = Query(min_length=1)):
    """
    Delete one entry everywhere: database row removed, every cache
    node copy invalidated, search caches for the entity purged.
    """

    try:
        result = _admin().delete_entry(name, key)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown dataset or key: {name} / {key}"
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except DatabaseUnavailableError:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return {"status": "deleted", **result}
