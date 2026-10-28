"""
Direct per-node cache access for the web UI (Lab 5 - live LRU view).

The client /cache API fans operations out through the hash ring: the
caller never chooses which node holds a key, so nobody can watch a
single node's doubly linked list reorder.  These routes proxy exactly
ONE pool port instead:

    GET    /cluster/nodes/{port}/cache-state           that node's list (MRU -> LRU)
    GET    /cluster/nodes/{port}/cache/{key}           read  (a hit hops the key to HEAD)
    PUT    /cluster/nodes/{port}/cache/{key}           write (overflow drops the TAIL node)
    DELETE /cluster/nodes/{port}/cache/{key}           explicit removal
    DELETE /cluster/nodes/{port}/cache-prefix/{prefix} drop a whole key prefix

No fan-out, no replication, no database: lab traffic stays local to
that node, which is exactly what a deterministic LRU demonstration
needs.  Every route is restricted to the node pool so the proxy can
never be aimed at an arbitrary host.
"""

import requests

from fastapi import HTTPException, Path
from urllib.parse import quote

from .app import app
from .models import CacheEntry
from .config import MAX_NODES, NODE_PORT_BASE, TIMEOUT


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def _pool() -> list:
    return [NODE_PORT_BASE + i for i in range(MAX_NODES)]


def _target(port: int) -> str:
    """Validate the port against the pool, then build its base URL."""

    if port not in _pool():

        raise HTTPException(
            status_code=400,
            detail=(
                f"port {port} is outside the node pool "
                f"{NODE_PORT_BASE}-{NODE_PORT_BASE + MAX_NODES - 1}"
            )
        )

    return f"http://127.0.0.1:{port}"


def _forward(port: int, method: str, path: str, **kwargs) -> dict:
    """
    Call one endpoint on one node and map its answer onto this API:
    JSON body through unchanged, 4xx/5xx passed through with the
    node's own detail, an unreachable node becomes 502.
    """

    url = f"{_target(port)}{path}"

    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail=f"node on port {port} is not reachable"
        )

    if response.status_code >= 400:

        try:
            detail = response.json().get("detail") or response.text
        except ValueError:
            detail = response.text or f"node returned {response.status_code}"

        raise HTTPException(
            status_code=response.status_code,
            detail=detail
        )

    try:
        return response.json()
    except ValueError:
        return {"status": "ok"}


def _key_path(key: str) -> str:
    """Re-encode a decoded path segment so keys keep every character."""

    return f"/cache/{quote(key, safe='')}"


# ---------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------

@app.get("/cluster/nodes/{port}/cache-state")
def node_cache_state(port: int = Path(ge=1, le=65535)):
    """Capacity, counters and keys in recency order (MRU -> LRU)."""

    return _forward(port, "GET", "/stats/lru")


@app.get("/cluster/nodes/{port}/cache/{key}")
def node_cache_get(
    key: str,
    port: int = Path(ge=1, le=65535)
):
    """Read one key off that node.  A 404 is a plain cache miss."""

    return _forward(port, "GET", _key_path(key))


@app.put("/cluster/nodes/{port}/cache/{key}")
def node_cache_set(
    entry: CacheEntry,
    key: str,
    port: int = Path(ge=1, le=65535)
):
    """Write one key into that node only (no fan-out, no database)."""

    return _forward(
        port, "PUT", _key_path(key),
        json={"value": entry.value, "ttl": entry.ttl}
    )


@app.delete("/cluster/nodes/{port}/cache/{key}")
def node_cache_delete(
    key: str,
    port: int = Path(ge=1, le=65535)
):
    """Explicitly remove one key from that node."""

    return _forward(port, "DELETE", _key_path(key))


@app.delete("/cluster/nodes/{port}/cache-prefix/{prefix:path}")
def node_cache_flush(
    prefix: str,
    port: int = Path(ge=1, le=65535)
):
    """Drop a whole key prefix on that node (lab cleanup)."""

    return _forward(
        port, "DELETE",
        f"/internal/prefix/{quote(prefix, safe='')}"
    )
