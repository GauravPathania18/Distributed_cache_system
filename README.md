# DCS — Distributed Cache System

An educational, from-scratch distributed cache written in Python.
It grows one layer at a time: a local LRU cache becomes a cluster of
nodes behind a router, with consistent hashing, replication, failure
detection, a database source of truth, and finally intelligent
recovery when the cluster restarts.

- **Language / runtime:** Python 3.10+
- **HTTP layer:** FastAPI + Uvicorn
- **Source of truth:** SQLite (no external services required)
- **Tests:** pytest — 170 tests

---

## Architecture

```
                         CLIENT
                            │
                            ▼
                     ┌────────────┐
                     │   ROUTER   │  :8000
                     │ NodeManager│  lifecycle: STARTING →
                     │ Hash Ring  │            RECOVERING → READY
                     │ Health Mon │                 UNHEALTHY
                     │ Replication│                      │
                     └─────┬──────┘                 FAILED
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      Node 1           Node 2           Node 3      :8001-8003
      LRUCache         LRUCache         LRUCache
          │                │                │
          └────────────────┴────────────────┘
                           │
        cache MISS ──► SingleFlight ──► CircuitBreaker ──► SQLite
                           ▲                               │
                           └──────── Cache Warm-up ◄───────┘
                                 (Layer 6.3, done)
```

The router is the only public entry point. Cache nodes are "dumb"
in-memory stores: they never talk to the database. The database is
always the source of truth; the cache is a disposable acceleration
layer that can be emptied and rebuilt at any time.

---

## Layer roadmap

| Layer | Topic | Status |
|---|---|---|
| 1 | Local LRU cache (TTL, stats, stampede protection) | Done |
| 2 | Cache node HTTP server (`main.py`) | Done |
| 3 | Consistent hashing + virtual nodes | Done |
| 4 | Replication, health checks, failure detection | Done |
| 5 | Database integration & cache-aside | Done |
| 5.7 | Circuit breaker (CLOSED / OPEN / HALF-OPEN) | Done |
| 5.8 | Single-flight request coalescing | Done |
| **6.1** | **Node lifecycle: STARTING → RECOVERING → READY → UNHEALTHY → FAILED** | **Done** |
| **6.2** | **Cache statistics & hot-key tracking** | **Done** |
| **6.3** | **Hot-key aggregation & cache warm-up (incl. the 6.4 rejoin gate)** | **Done** |
| 7.1 | Replica selection (placement) | Next |
| 7.2 | Replicated writes (primary → replicas) | Planned |
| 7.3 | Replication log, versions & sync | Planned |
| 7.4 | Failure detection formalized | Planned |
| 7.5 | Automatic failover | Planned |
| 7.6 | Re-replication & rebalancing | Planned |

### Layer 6.1 in one paragraph

RAM is volatile: after a restart the cache is cold and the database
can be hammered by a flood of misses. Nodes therefore have a
lifecycle instead of a simple up/down flag. Only `READY` (and
temporarily `UNHEALTHY`) nodes sit in the hash ring. A node that
recovers from a failure must spend `RECOVERY_THRESHOLD` consecutive
healthy checks in `RECOVERING` before it is allowed to receive
traffic again, so a cold node is never slammed all at once.

### Layer 6.2 in one paragraph

A cache node counts its own operations (hits, misses, sets,
deletes, evictions) and its per-key access frequency entirely in
memory — measuring usage must never add writes to the database.
The key tracker is bounded (`max_tracked_keys`) so it cannot
become a second memory problem, and counters are reset every
5-minute window so "hot keys" means *recently* hot. The ranked
key list produced here is the raw material Layer 6.3 needs
to decide what to warm up after a node restarts.

### Layer 6.3 in one paragraph

A restarted node is empty, but the cluster remembers what is hot:
the router records every key it proxies into its own bounded
ranking (which survives node restarts) and folds in each node's
Layer 6.2 window before warming. When a node sits in `RECOVERING`,
the router reloads the hottest keys it *would own* (ownership is
computed on a ring view that includes the gated node) — read from
the database, throttled to `WARMUP_RATE_LIMIT` reads/second,
written directly to the recovering node. Only then, together with
`RECOVERY_THRESHOLD` healthy checks, does the node become `READY`
and rejoin the ring. Missing keys are skipped (the database stays
the source of truth), and a database outage keeps the node in
`RECOVERING` with retries every health pass — warm-up is an
optimization, never a correctness blocker.

---

## Quick start

```powershell
# 1. Environment (one time)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Terminal 1-3: cache nodes
python main.py --port 8001 --capacity 100
python main.py --port 8002 --capacity 100
python main.py --port 8003 --capacity 100

# 3. Terminal 4: router (do NOT use --reload)
uvicorn router.router:app --port 8000

# 4. Watch the cluster come up
curl http://localhost:8000/cluster/status
```

> Right after the router starts, nodes are gated
> (`starting` → `recovering` → `ready`) and the ring is empty.
> PUTs return 503 until the nodes are `ready`; wait for
> `/cluster/status` to list them under `active_nodes`.

Configuration (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `CACHE_NODES` | `8001,8002,8003` | Comma-separated node URLs |
| `CACHE_DB_PATH` | `data/cache.db` | SQLite location |
| `RECOVERY_THRESHOLD` | `2` | Healthy checks required in `RECOVERING` before `READY` |
| `CLUSTER_BOOTSTRAP_STATE` | `starting` | Initial state of declared nodes (`starting` / `ready`) |
| `WARMUP_ENABLED` | `on` | Warm-up master switch (off = ungated READY) |
| `WARMUP_LIMIT` | `1000` | Top-K keys reloaded during warm-up |
| `WARMUP_RATE_LIMIT` | `100` | Max database reads/second during warm-up |
| `HOTKEY_MAX_KEYS` | `10000` | Bound on the router-side hot-key ranking |

---

## Tests

```powershell
pytest -v
```

Expected: **170 passed** — 14 cache + 16 consistent hash + 8 router +
15 repository + 13 cache-service + 11 circuit-breaker + 5
single-flight + 28 node lifecycle + 14 dataset import +
15 statistics + 21 warm-up + 10 dataset generator.

Demos: `python demo.py` (Layer 1), `python demo_hash.py` (Layer 3),
`python demo_router.py` (Layer 4/5).

No dataset handy? Generate synthetic ones (deterministic, CSV/JSON/JSONL):

```powershell
python generate_dataset.py --kind all --count 1000
```

Have a dataset from Kaggle or elsewhere? Load it into the database:

```powershell
python import_dataset.py --file users.csv --prefix user:
```

Full command reference, curl examples and live fault-tolerance
walkthroughs live in [`commands.md`](commands.md).

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
