import os

import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Optional

from cluster.node_manager import NodeManager


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


# ---------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------

app = FastAPI(
    title="Distributed Cache Router",
    description="Layer 4 - Router with Failure Detection & Replication",
    version="2.0"
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


node_manager = NodeManager(
    nodes=nodes_from_env(),
    virtual_nodes=VIRTUAL_NODES,
    replication_factor=REPLICATION_FACTOR,
    health_interval=HEALTH_INTERVAL,
    failure_threshold=FAILURE_THRESHOLD
)

node_manager.start_health_monitor()

session = requests.Session()


# ---------------------------------------------------------
# REQUEST MODEL
# ---------------------------------------------------------

class CacheEntry(BaseModel):

    value: Any

    ttl: Optional[int] = None


# ---------------------------------------------------------
# GET (primary -> replica fallback)
# ---------------------------------------------------------

@app.get("/cache/{key}")
def get_cache(key: str):

    nodes = node_manager.get_nodes(key)

    if not nodes:

        raise HTTPException(
            status_code=503,
            detail="No cache nodes available"
        )

    # Try primary first, then replicas
    for node in nodes:

        try:

            response = session.get(
                f"{node}/cache/{key}",
                timeout=TIMEOUT
            )

            if response.status_code == 200:

                return {
                    "node": node,
                    "data": response.json()
                }

            # 404 on this node -> try next
            if response.status_code == 404:

                continue

        except requests.RequestException:

            continue

    # All nodes missed or failed
    raise HTTPException(
        status_code=404,
        detail="Cache miss"
    )


# ---------------------------------------------------------
# PUT (replicate to primary + replicas)
# ---------------------------------------------------------

@app.put("/cache/{key}")
async def set_cache(
    key: str,
    request: Request
):

    nodes = node_manager.get_nodes(key)

    if not nodes:

        raise HTTPException(
            status_code=503,
            detail="No cache nodes available"
        )

    body = await request.json()

    successful_nodes = []
    errors = []

    # Write to every node in the replica set
    for node in nodes:

        try:

            response = session.put(
                f"{node}/cache/{key}",
                json=body,
                timeout=TIMEOUT
            )

            if response.status_code == 200:

                successful_nodes.append(node)

            else:

                errors.append(
                    f"{node}: HTTP {response.status_code}"
                )

        except requests.RequestException as e:

            errors.append(f"{node}: {str(e)}")

    # Require at least one successful copy
    if not successful_nodes:

        raise HTTPException(
            status_code=503,
            detail="All cache nodes failed"
        )

    return {
        "status": "stored",
        "key": key,
        "replicated_to": len(successful_nodes),
        "nodes": successful_nodes
    }


# ---------------------------------------------------------
# DELETE (invalidate on primary + replicas)
# ---------------------------------------------------------

@app.delete("/cache/{key}")
def delete_cache(key: str):

    nodes = node_manager.get_nodes(key)

    if not nodes:

        raise HTTPException(
            status_code=503,
            detail="No cache nodes available"
        )

    deleted_from = []

    for node in nodes:

        try:

            response = session.delete(
                f"{node}/cache/{key}",
                timeout=TIMEOUT
            )

            # 200 = deleted, 404 = already gone — both count
            if response.status_code in [200, 404]:

                deleted_from.append(node)

        except requests.RequestException:

            continue

    return {
        "status": "deleted",
        "key": key,
        "nodes": deleted_from
    }


# ---------------------------------------------------------
# CLUSTER STATUS
# ---------------------------------------------------------

@app.get("/cluster/status")
def cluster_status():

    return node_manager.get_status()


# ---------------------------------------------------------
# ROUTER HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "router"
    }
