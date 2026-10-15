from fastapi import HTTPException, Request

from database.exceptions import DatabaseUnavailableError

from .app import app
from . import _components


@app.get("/search/entities")
def search_entities():
    """
    Layer 8 - what the UI needs to build filter sidebars: per-entity
    filters, sortable columns and suggest fields.
    """

    return {"entities": _components.search_service.entities()}


@app.get("/search/{entity}")
def search_entity(entity: str, request: Request):
    """
    Layer 8 - cached search.

    Every distinct combination of keyword + filters + sort + page is
    one cache key. The first search for a combination runs SQL and
    writes the result set through the normal write-through path
    (database row + fan-out + replication); every repeat is a pure
    cache hit.

    Response carries `source` (`cache` | `database`), the `cache_key`
    that was used, `took_ms` for this request and `db_took_ms` for
    the SQL that originally produced the page.
    """

    try:

        result = _components.search_service.search(
            entity, dict(request.query_params)
        )

    except ValueError as error:

        raise HTTPException(status_code=422, detail=str(error))

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - try again later"
        )

    return result


@app.get("/search/{entity}/suggest")
def search_suggest(entity: str, field: str, q: str = ""):
    """Layer 8 - distinct-value autocomplete for one facet field."""

    try:

        result = _components.search_service.suggest(entity, field, q)

    except ValueError as error:

        raise HTTPException(status_code=422, detail=str(error))

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - try again later"
        )

    return result


@app.get("/browse/prefix")
def browse_prefix(prefix: str = "", limit: int = 50):
    """
    Layer 8 - key listing for the inspector.

    Deliberately uncached so an inspection always reflects the live
    cluster; a stale key list would be worse than none.
    """

    try:

        return _components.search_service.browse(prefix, limit)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable - try again later"
        )
