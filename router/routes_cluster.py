import requests

from fastapi import HTTPException, Query

from .app import app
from . import _components


@app.get("/cluster/status")
def cluster_status():

    return _components.node_manager.get_status()


@app.get("/cluster/hot-keys")
def cluster_hot_keys(
    limit: int = Query(default=10, ge=1, le=10000),
    per_node: int = Query(default=100, ge=1, le=1000)
):
    """
    Layer 6.3 - merged cluster hot-key ranking.

    Folds a fresh Layer 6.2 top-keys snapshot from every active
    node into the router's own ranking (the node that just
    restarted no longer has its window - the router's view does),
    then returns the hottest keys plus a per-node breakdown.
    """

    with _components.node_manager.lock:
        active = sorted(_components.node_manager.active_nodes)

    per_node_stats = {}

    for node in active:

        try:

            response = requests.get(
                f"{node}/stats/top-keys",
                params={"limit": per_node},
                timeout=1
            )

            if response.status_code == 200:

                data = response.json()
                counts = data.get("top_keys") or []

                _components.hot_keys.merge(counts)

                per_node_stats[node] = {
                    "top_keys": counts,
                    "tracked_keys": data.get("tracked_keys")
                }

            else:
                per_node_stats[node] = {
                    "error": f"HTTP {response.status_code}"
                }

        except requests.RequestException as error:
            per_node_stats[node] = {
                "error": error.__class__.__name__
            }

    stats = _components.hot_keys.stats()

    return {
        "top_keys": _components.hot_keys.top_keys(limit),
        "tracked_keys": stats["tracked_keys"],
        "max_keys": stats["max_keys"],
        "untracked": stats["untracked"],
        "nodes_queried": len(active),
        "per_node": per_node_stats
    }


@app.get("/cluster/warmup")
def cluster_warmup_status():
    """
    Layer 6.3 - warm-up state per node.

    A node only reaches READY (and rejoins the hash ring) once its
    record is done - warm-up is an optimization gate, not a
    correctness one.
    """

    return {
        "enabled": _components.warmup.enabled,
        "limit": _components.warmup.limit,
        "rate_limit": _components.warmup.rate_limit,
        "hot_keys": _components.hot_keys.stats(),
        "nodes": _components.warmup.status()
    }


@app.post("/cluster/warmup/{node:path}")
def cluster_warmup_trigger(node: str):
    """Manually start (or re-request) a warm-up for one node."""

    with _components.node_manager.lock:
        known = node in _components.node_manager.nodes

    if not known:

        raise HTTPException(
            status_code=404,
            detail=f"unknown node: {node}"
        )

    started = _components.warmup.ensure(node)

    entry = _components.warmup.status().get(node, {})

    return {
        "node": node,
        "started": started,
        "state": entry.get("state")
    }


@app.get("/circuit-breaker/status")
def circuit_breaker_status():

    return _components.cache_service.circuit_breaker_status()


# ---------------------------------------------------------
# LAYER 7.3/7.5 - REPLICATION STATUS + SYNC
# ---------------------------------------------------------

@app.get("/cluster/replication")
def cluster_replication_status():
    """
    Layer 7.3/7.5 - replication log, per-node ack high-water marks
    and the last observed failover.
    """

    return _components.replication.status()


@app.post("/cluster/resync/{node:path}")
def cluster_resync(node: str):
    """
    Layer 7.3 - synchronously bring one node's data current.

    Picks the cheaper correct strategy itself: incremental log
    replay when the node is only a little behind, full DB snapshot
    when the gap is large or the log rolled over.
    """

    with _components.node_manager.lock:
        known = node in _components.node_manager.nodes

    if not known:

        raise HTTPException(
            status_code=404,
            detail=f"unknown node: {node}"
        )

    result = _components.replication.sync_node(node)

    return {"node": node, **result}


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "router"
    }
