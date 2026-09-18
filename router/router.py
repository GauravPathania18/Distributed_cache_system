import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

from cluster.node_manager import NodeManager
from database.exceptions import DatabaseUnavailableError
from database.repository import DatabaseRepository
from services.cache_service import CacheService
from services.distributed_cache import DistributedCache


# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

DEFAULT_NODES = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
]

VIRTUAL_NODES = 100
REPLICATION_FACTOR = 2
HEALTH_INTERVAL = 2
FAILURE_THRESHOLD = 3
TIMEOUT = 2

DEFAULT_DB_PATH = os.environ.get("CACHE_DB_PATH", "data/cache.db")


# ---------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------

app = FastAPI(
    title="Distributed Cache Router",
    description="Layer 5.7/5.8 - Cache-Aside, Database fallback, Circuit Breaker & Single-Flight",
    version="3.1"
)


def nodes_from_env() -> list:
    """Read the cluster node list from the CACHE_NODES env var (optional)."""

    raw = os.environ.get("CACHE_NODES")

    if raw:
        return [
            node.strip()
            for node in raw.split(",")
            if node.strip()
        ]

    return list(DEFAULT_NODES)


# ---------------------------------------------------------
# COMPONENTS
# ---------------------------------------------------------

# Source of truth. Shared by the whole router process.
database = DatabaseRepository(db_path=DEFAULT_DB_PATH)

node_manager = NodeManager(
    nodes=nodes_from_env(),
    virtual_nodes=VIRTUAL_NODES,
    replication_factor=REPLICATION_FACTOR,
    health_interval=HEALTH_INTERVAL,
    failure_threshold=FAILURE_THRESHOLD
)

node_manager.start_health_monitor()

# Data-plane client over the cluster.
distributed_cache = DistributedCache(
    node_manager=node_manager,
    timeout=TIMEOUT
)

# Layer 5 cache-aside policy.
cache_service = CacheService(
    cache=distributed_cache,
    database=database
)


def configure_cluster(
    nodes,
    replication_factor=REPLICATION_FACTOR,
    virtual_nodes=VIRTUAL_NODES,
    health_interval=HEALTH_INTERVAL,
    failure_threshold=FAILURE_THRESHOLD,
    start_monitor=False
):
    """Rebuild the cluster components (used by tests / demos)."""

    global node_manager, distributed_cache, cache_service

    node_manager = NodeManager(
        nodes=nodes,
        virtual_nodes=virtual_nodes,
        replication_factor=replication_factor,
        health_interval=health_interval,
        failure_threshold=failure_threshold
    )

    if start_monitor:
        node_manager.start_health_monitor()

    distributed_cache = DistributedCache(
        node_manager=node_manager,
        timeout=TIMEOUT
    )

    cache_service = CacheService(
        cache=distributed_cache,
        database=database
    )


# ---------------------------------------------------------
# REQUEST MODEL
# ---------------------------------------------------------

class CacheEntry(BaseModel):

    value: Any

    ttl: Optional[int] = None


# ---------------------------------------------------------
# GET (cache-aside: cache -> database -> populate cache)
# ---------------------------------------------------------

@app.get("/cache/{key}")
def get_cache(key: str):

    try:

        result = cache_service.get(key)

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


# ---------------------------------------------------------
# PUT (write-through: database then cache)
# ---------------------------------------------------------

@app.put("/cache/{key}")
def set_cache(
    key: str,
    entry: CacheEntry
):

    try:

        result = cache_service.set(
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


# ---------------------------------------------------------
# DELETE (database then invalidate cache)
# ---------------------------------------------------------

@app.delete("/cache/{key}")
def delete_cache(key: str):

    try:

        result = cache_service.delete(key)

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


# ---------------------------------------------------------
# DATABASE (temporary - direct access for testing/debugging)
# NOTE: to be removed once the cache-aside flow is proven.
# ---------------------------------------------------------

@app.get("/db/{key}")
def db_get(key: str):

    try:

        value = database.get(key)

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

        deleted = database.delete(key)

    except DatabaseUnavailableError:

        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    if not deleted:

        raise HTTPException(
            status_code=404,
            detail="Key not found in database"
        )

    return {
        "status": "deleted",
        "key": key,
        "source": "database"
    }


# ---------------------------------------------------------
# CLUSTER STATUS
# ---------------------------------------------------------

@app.get("/cluster/status")
def cluster_status():

    return node_manager.get_status()


# ---------------------------------------------------------
# CIRCUIT BREAKER STATUS
# ---------------------------------------------------------

@app.get("/circuit-breaker/status")
def circuit_breaker_status():

    return cache_service.circuit_breaker_status()


# ---------------------------------------------------------
# ROUTER HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "router"
    }