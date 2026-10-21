"""
Layer 9 - cluster control from the web UI.

    GET  /cluster/nodes                 what runs where (state, process, stats)
    POST /cluster/nodes {target: N}     scale the cluster to N nodes
    POST /cluster/nodes/{port}/start    spawn (or adopt) one node
    POST /cluster/nodes/{port}/stop     deregister + stop one node
    POST /cluster/nodes/{port}/restart  stop, then start

Membership bookkeeping lives here; process mechanics live in
services/process_manager.py.  A freshly registered node joins as
STARTING and is driven by the existing health monitor through
RECOVERING -> warm-up/seeding -> READY - scaling up is just
register_node, scaling down deregister_node + stop.
"""

import time

import requests

from fastapi import HTTPException, Path
from pydantic import BaseModel, Field
from urllib.parse import urlparse

from cluster.health_monitor import check_node
from services.process_manager import process_manager

from .app import app
from . import _components
from .config import (
    MAX_NODES,
    NODE_PORT_BASE,
    NODE_CAPACITY,
    NODE_START_TIMEOUT,
)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def _url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _port_of(url: str):
    try:
        return urlparse(url).port
    except ValueError:
        return None


def _pool() -> list:
    return [NODE_PORT_BASE + i for i in range(MAX_NODES)]


def _node_stats(url: str):
    """Best-effort /stats snapshot (doubles as a liveness probe)."""

    try:
        response = requests.get(f"{url}/stats", timeout=1)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass

    return None


def _node_entry(url: str, states: dict, active: list) -> dict:

    port = _port_of(url)
    owned = port is not None and process_manager.is_running(port)
    stats = _node_stats(url)
    alive = stats is not None

    if alive and owned:
        process = "owned"
    elif alive:
        process = "attached"
    else:
        process = "stopped"

    return {
        "url": url,
        "port": port,
        "state": states.get(url, "unknown"),
        "active": url in active,
        "process": process,
        "pid": process_manager.pid(port) if (owned or alive) else None,
        "stats": stats,
    }


def _wait_healthy(urls: list, timeout=None) -> dict:
    """Poll /health of every url until all answer or time runs out."""

    deadline = time.time() + (
        NODE_START_TIMEOUT if timeout is None else timeout
    )

    pending = set(urls)
    result = {}

    while pending and time.time() < deadline:

        for url in list(pending):
            if check_node(url):
                result[url] = True
                pending.discard(url)

        if pending:
            time.sleep(0.2)

    for url in pending:
        result[url] = False

    return result


def _shutdown_node(url: str) -> bool:
    """Ask a node to exit gracefully through its internal endpoint."""

    try:
        response = requests.post(f"{url}/internal/shutdown", timeout=1)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _start_node(port: int) -> dict:
    """
    Make `port` a cluster member: adopt it when something already
    answers there (never spawn a second process on a live port),
    otherwise spawn main.py and register it as STARTING.
    """

    url = _url_for(port)

    if check_node(url):

        if url not in _components.node_manager.nodes:
            _components.node_manager.register_node(url)

        return {"url": url, "port": port, "action": "attached"}

    result = process_manager.start(port, capacity=NODE_CAPACITY)

    if not (result.get("started") or result.get("already_running")):
        return {
            "url": url, "port": port, "action": "failed",
            "error": result.get("error") or "could not spawn node",
        }

    if url not in _components.node_manager.nodes:
        _components.node_manager.register_node(url)

    return {
        "url": url,
        "port": port,
        "action": "started" if result.get("started") else "already-running",
        "pid": result.get("pid"),
        "log": result.get("log"),
    }


def _stop_node(port: int) -> dict:
    """
    Take `port` out of the cluster and stop its process.

    Order matters: deregister FIRST so no request is routed to a
    node that is about to die.  The process is asked to shut down
    gracefully when it answers HTTP; only a hung process falls back
    to the mechanical (child terminate / PID file) stop.
    """

    url = _url_for(port)
    manager = _components.node_manager

    removed = False

    if url in manager.nodes:
        removed = manager.deregister_node(url)
        _components.replication.forget_node(url)
        _components.warmup.forget(url)

    graceful = _shutdown_node(url)

    if graceful:

        for _ in range(5):
            if not check_node(url):
                break
            time.sleep(0.3)

        result = process_manager.stop(port, force=False)

    else:

        result = process_manager.stop(port, force=True)

    return {
        "url": url,
        "port": port,
        "removed_from_cluster": removed,
        "shutdown_requested": graceful,
        "process": result,
        "state": manager.get_node_state(url) if url in manager.nodes else None,
    }


# ---------------------------------------------------------
# REQUEST MODELS
# ---------------------------------------------------------

class ScaleRequest(BaseModel):
    target: int = Field(ge=0, le=MAX_NODES)


# ---------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------

@app.get("/cluster/nodes")
def cluster_nodes():
    """
    Every known node with lifecycle state, process info (owned /
    attached / stopped) and live cache stats for the UI cards.
    """

    status = _components.node_manager.get_status()

    states = status["node_states"]
    active = status["active_nodes"]

    nodes = [_node_entry(url, states, active) for url in states]
    nodes.sort(key=lambda node: node["port"] or 0)

    return {
        "nodes": nodes,
        "total": status["total_nodes"],
        "active": len(active),
        "max_nodes": MAX_NODES,
        "node_port_base": NODE_PORT_BASE,
        "node_capacity": NODE_CAPACITY,
        "pool": _pool(),
    }


@app.post("/cluster/nodes")
def cluster_scale(body: ScaleRequest):
    """
    Scale the cluster to `target` nodes.

    Grow: spawn/adopt pool ports and register them (they gate in
    through health -> warm-up -> READY).  Shrink: remove the
    highest-port members from the ring immediately, then stop
    their processes - no draining, cached rows on a removed node
    simply live on its RF=2 replica or fall back to the database.
    """

    target = body.target
    manager = _components.node_manager

    added, removed, failed, health = [], [], [], {}

    current = sorted(
        manager.nodes,
        key=lambda url: _port_of(url) or 0,
        reverse=True
    )

    # ---- shrink: highest ports leave first ----------------
    if target < len(current):

        for url in current[: len(current) - target]:
            removed.append(_stop_node(_port_of(url) or 0))

    # ---- grow or hold steady: make `target` members RUN ---
    #
    # A configured-but-dead member (the router boots with the
    # default node list registered) counts towards the target but
    # must still be started - "3 nodes" should mean 3 RUNNING
    # nodes, not three tombstones.  _start_node adopts anything
    # that already answers, so a live port is never spawned twice.
    else:

        started_urls = []

        for port in _pool():

            url = _url_for(port)
            is_member = url in manager.nodes

            if len(manager.nodes) >= target and not is_member:
                break

            if is_member and check_node(url):
                continue    # already answering - nothing to do

            record = _start_node(port)

            (failed if record["action"] == "failed" else added).append(record)

            if record["action"] != "failed":
                started_urls.append(record["url"])

        if started_urls:
            health = _wait_healthy(started_urls)

        for record in added:
            record["healthy"] = health.get(record["url"])

    return {
        "target": target,
        "added": added,
        "removed": removed,
        "failed": failed,
        "health": health,
        "total": manager.get_status()["total_nodes"],
        "active": len(manager.get_status()["active_nodes"]),
    }


@app.post("/cluster/nodes/{port}/start")
def cluster_node_start(port: int = Path(ge=1, le=65535)):
    """Spawn (or adopt) one node and wait briefly for /health."""

    if port not in _pool():
        raise HTTPException(
            status_code=400,
            detail=(
                f"port {port} is outside the node pool "
                f"{NODE_PORT_BASE}-{NODE_PORT_BASE + MAX_NODES - 1}"
            )
        )

    record = _start_node(port)

    if record["action"] == "failed":
        raise HTTPException(status_code=500, detail=record["error"])

    record["healthy"] = _wait_healthy([record["url"]])[record["url"]]
    record["state"] = _components.node_manager.get_node_state(record["url"])

    return record


@app.post("/cluster/nodes/{port}/stop")
def cluster_node_stop(port: int = Path(ge=1, le=65535)):
    """Deregister from the ring and stop the process on `port`."""

    return _stop_node(port)


@app.post("/cluster/nodes/{port}/restart")
def cluster_node_restart(port: int = Path(ge=1, le=65535)):
    """Stop, then start again - membership re-enters through STARTING."""

    if port not in _pool():
        raise HTTPException(
            status_code=400,
            detail=(
                f"port {port} is outside the node pool "
                f"{NODE_PORT_BASE}-{NODE_PORT_BASE + MAX_NODES - 1}"
            )
        )

    stopped = _stop_node(port)
    started = _start_node(port)

    if started["action"] == "failed":
        raise HTTPException(status_code=500, detail=started["error"])

    started["healthy"] = _wait_healthy([started["url"]])[started["url"]]
    started["state"] = _components.node_manager.get_node_state(started["url"])
    started["stopped"] = stopped

    return started
