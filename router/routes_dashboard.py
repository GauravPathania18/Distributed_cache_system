import time

import requests

from .app import app
from .config import nodes_from_env
from . import _components


def _node_snapshot(node: str) -> dict:
    """
    Pull one node's lifetime + windowed stats.

    Per-node failures are reported inline (never raised) so a dead
    node degrades one panel instead of blanking the dashboard -
    the same contract /cluster/hot-keys already uses.
    """

    out = {"lifetime": None, "window": None, "error": None}

    try:

        response = requests.get(f"{node}/stats", timeout=2)

        if response.status_code != 200:

            out["error"] = f"HTTP {response.status_code}"

            return out

        out["lifetime"] = response.json()

    except requests.RequestException as error:

        out["error"] = error.__class__.__name__

        return out

    try:

        response = requests.get(
            f"{node}/stats/window",
            params={"limit": 5},
            timeout=2
        )

        if response.status_code == 200:

            out["window"] = response.json()

    except requests.RequestException:
        pass

    return out


@app.get("/dashboard/aggregate")
def dashboard_aggregate():
    """
    Layer 8 - one call for the whole dashboard.

    Fans out to every node for its lifetime and windowed stats, then
    folds those together with the router-side cluster status, circuit
    breaker, hot keys, warm-up, replication and Layer 8 telemetry.

    Hit-rate units are NORMALIZED HERE: a node's `/stats` reports a
    percentage (0-100) while `/stats/window` reports a fraction
    (0-1). Every field this endpoint returns is a percentage, so the
    frontend never has to guess.

    `telemetry.db_reads` / `db_reads_avoided` are the authoritative
    database-load numbers: they count actual SQL queries the router
    issued, not HTTP requests (single-flight coalescing is already
    accounted for). The summed node stats describe the cache tier
    and are shown separately on purpose.
    """

    nodes = {}

    for node in nodes_from_env():

        nodes[node] = _node_snapshot(node)

    lifetime_hits = 0
    lifetime_misses = 0
    lifetime_size = 0
    lifetime_capacity = 0
    lifetime_evictions = 0

    window_hits = 0
    window_misses = 0

    for snapshot in nodes.values():

        lifetime = snapshot.get("lifetime") or {}

        lifetime_hits += lifetime.get("hits", 0) or 0
        lifetime_misses += lifetime.get("misses", 0) or 0
        lifetime_size += lifetime.get("size", 0) or 0
        lifetime_capacity += lifetime.get("capacity", 0) or 0
        lifetime_evictions += lifetime.get("evictions", 0) or 0

        window = snapshot.get("window") or {}

        window_hits += window.get("hits", 0) or 0
        window_misses += window.get("misses", 0) or 0

    lifetime_total = lifetime_hits + lifetime_misses

    window_total = window_hits + window_misses

    return {
        "generated_at": time.time(),

        "telemetry": _components.telemetry.stats(),

        "aggregate": {
            "hits": lifetime_hits,
            "misses": lifetime_misses,
            "hit_rate_pct": (
                round(lifetime_hits / lifetime_total * 100, 2)
                if lifetime_total else 0.0
            ),
            "size": lifetime_size,
            "capacity": lifetime_capacity,
            "evictions": lifetime_evictions,
            "window_hits": window_hits,
            "window_misses": window_misses,
            "window_hit_rate_pct": (
                round(window_hits / window_total * 100, 2)
                if window_total else 0.0
            ),
        },

        "nodes": nodes,

        "cluster": _components.node_manager.get_status(),

        "circuit_breaker": _components.cache_service.circuit_breaker_status(),

        "hot_keys": _components.hot_keys.stats(),

        "top_keys": _components.hot_keys.top_keys(15),

        "warmup": {
            "enabled": _components.warmup.enabled,
            "nodes": _components.warmup.status(),
        },

        "replication": _components.replication.status(),
    }


@app.post("/dashboard/telemetry/reset")
def dashboard_telemetry_reset():
    """Layer 8 - zero the read counters (used by the Cache Lab demo)."""

    _components.telemetry.reset()

    return {"status": "reset", "telemetry": _components.telemetry.stats()}
