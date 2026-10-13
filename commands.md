# Distributed Cache — Command Reference

All commands are run from the project root: `E:\OneDrive\Desktop\DCS`.
The project needs its virtual environment activated before every step.

---

## 0. Environment Setup (one time)

```powershell
# Create the virtual environment (skip if .venv already exists)
python -m venv .venv

# Activate it — Windows PowerShell
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

You should see `(venv)` in your prompt. Re-activate it whenever you
open a new terminal. The venv is NOT part of the project code — it
only holds the installed packages.

Project layout after setup:

```
DCS/
├── .venv/                <- virtual environment (packages)
├── cache/                <- Layer 1 + 6.2/6.3: LRU cache engine
│   ├── __init__.py
│   ├── hot_keys.py         <- Layer 6.3: cluster-wide hot-key ranking
│   ├── lru_cache.py
│   ├── node.py
│   └── statistics.py       <- Layer 6.2: hit/miss counters + hot keys
├── cluster/              <- Layer 4 + 6.1/6.3: cluster management
│   ├── __init__.py
│   ├── consistent_hash.py   <- hashing + ring views (with_node)
│   ├── events.py            <- Layer 6.3: lifecycle event bus
│   ├── health_monitor.py    <- periodic checks -> lifecycle driver
│   ├── node_manager.py      <- membership, states, ring, gate, listeners
│   ├── node_state.py        <- NodeState enum + transition table
│   ├── node_lifecycle.py    <- STARTING/RECOVERING/READY/... logic
│   └── node_activation.py   <- legacy Layer 4 wrappers
├── router/               <- Layers 4-5: replication-aware + cache-aside router
│   ├── __init__.py
│   ├── app.py / config.py / models.py
│   ├── routes_cache.py / routes_cluster.py / routes_db.py
│   ├── _components.py       <- shared database / node_manager / services
│   └── router.py            <- public re-export shim
├── database/             <- Layer 5: SQLite source of truth
│   ├── __init__.py
│   ├── exceptions.py         <- DatabaseUnavailableError
│   └── repository.py
├── services/             <- Layer 5 + 6.3: cache-aside orchestration
│   ├── __init__.py
│   ├── cache_service.py      <- cache-aside policy + stampede protection
│   ├── circuit_breaker.py    <- CLOSED/OPEN/HALF-OPEN DB failure protection
│   ├── distributed_cache.py  <- data-plane client + write_to_node
│   ├── single_flight.py      <- request coalescing (leader/follower)
│   └── warmup.py             <- Layer 6.3: CacheWarmup + rate limiter
├── tests/                <- test suite
│   ├── conftest.py         <- points tests at a throwaway database
│   ├── test_cache.py
│   ├── test_consistent_hash.py
│   ├── test_router.py
│   ├── test_repository.py
│   ├── test_cache_service.py
│   ├── test_circuit_breaker.py
│   ├── test_single_flight.py
│   ├── test_node_lifecycle.py
│   ├── test_statistics.py
│   ├── test_warmup.py
│   ├── test_import_dataset.py
│   └── test_generate_dataset.py
├── data/                 <- generated SQLite DB (gitignored)
│   └── cache.db
├── datasets/             <- generated sample datasets (gitignored)
│   ├── users.csv
│   ├── products.json
│   └── orders.jsonl
├── main.py               <- Layer 2: cache node server
├── import_dataset.py     <- load CSV/JSON/JSONL datasets into SQLite
├── generate_dataset.py   <- synthesize test datasets for import_dataset.py
├── demo.py               <- Layer 1 demo
├── demo_hash.py          <- Layer 3.1 demo
├── demo_router.py        <- Layer 3.2 demo
├── requirements.txt
├── README.md
└── commands.md
```

---

## Layer 1 — Local LRU Cache Engine

```powershell
# Run the interactive demo
python demo.py

# Run the full test suite
pytest tests/test_cache.py -v
```

Expected: 14 tests pass (LRU eviction, TTL, stats, cache-stampede
prevention with get_or_set, etc.).

---

## Layer 2 — Cache Node (single process)

Start one cache node:

```powershell
python main.py --port 8001 --capacity 100
```

Then in a second terminal:

```powershell
# Open the interactive API docs
# http://localhost:8001/docs

# Health check
curl http://localhost:8001/health

# SET a value with a 60s TTL
curl -X PUT http://localhost:8001/cache/user:101 `
  -H "Content-Type: application/json" `
  -d '{"value":{"name":"Gaurav","age":21},"ttl":60}'

# GET it back
curl http://localhost:8001/cache/user:101

# EXISTS check
curl http://localhost:8001/cache/user:101/exists

# DELETE
curl -X DELETE http://localhost:8001/cache/user:101

# Stats (hits / misses / hit_rate / evictions)
curl http://localhost:8001/stats

# Layer 6.2 - current statistics window + hottest keys
curl http://localhost:8001/stats/window
curl "http://localhost:8001/stats/window?limit=5"
curl "http://localhost:8001/stats/top-keys?limit=10"
```

Missing keys return HTTP 404 `{"detail": "Cache miss"}`.

---

## Layer 3.1 — Consistent Hashing + Virtual Nodes

No servers needed. This is a pure data-structure demo:

```powershell
# Print the ring + key-to-node mapping
python demo_hash.py

# Run the consistent-hash unit tests
pytest tests/test_consistent_hash.py -v
```

Expected: 16 tests pass, including `get_nodes()` tests for
replica selection with distinct physical nodes.

---

## Layer 4 — Failure Detection, Node Management & Replication

You need **4 terminals**. Run these one per terminal:

### Terminal 1 — Node 1

```powershell
python main.py --port 8001 --capacity 100
```

### Terminal 2 — Node 2

```powershell
python main.py --port 8002 --capacity 100
```

### Terminal 3 — Node 3

```powershell
python main.py --port 8003 --capacity 100
```

### Terminal 4 — Router (public entry point)

**Do NOT use `--reload`** because the router starts a background
health-monitoring thread.

```powershell
uvicorn router.router:app --port 8000
```

> **Layer 6.1 note — gated startup:** the router does NOT send
> traffic to a node the moment it boots. Declared nodes start as
> `starting`, become `recovering` on their first healthy check, and
> only enter the hash ring as `ready` after `RECOVERY_THRESHOLD`
> (default 2) consecutive healthy checks. With a 2-second health
> interval that is roughly 4 seconds — longer if dead ports are
> dropped rather than refused (see the timing note in the
> fault-tolerance demo below). During that window the ring
> is empty: GETs fall through to the database and PUTs return 503.
> Wait for `/cluster/status` to list the nodes under
> `active_nodes` before running the demo below.

Architecture now running:

```
                         CLIENT
                            │
                            ▼
                     ┌────────────┐
                     │   ROUTER   │
                     │   :8000    │
                     └─────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ NodeManager │
                    │  Hash Ring  │
                    │ Health Chk  │
                    │ Replication │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          Node 1        Node 2       Node 3
          :8001         :8002        :8003
             │             │             │
          LRUCache      LRUCache      LRUCache
             │             │             │
             └─────────────┴─────────────┘
                     replication
```

### Check cluster status

```powershell
curl http://localhost:8000/cluster/status
```

Returns:

```json
{
    "total_nodes": 3,
    "active_nodes": ["http://localhost:8001", ...],
    "inactive_nodes": [],
    "node_states": {
        "http://localhost:8001": "ready",
        "http://localhost:8002": "ready",
        "http://localhost:8003": "ready"
    },
    "recovering_nodes": [],
    "failed_nodes": [],
    "replication_factor": 2,
    "health_interval": 2,
    "failure_threshold": 3,
    "recovery_threshold": 2
}
```

`active_nodes` = nodes currently in the hash ring (`ready` +
`unhealthy`). `node_states` shows the precise lifecycle state of
every known node — see Layer 6.1.

### PUT (replicated to primary + replica)

```powershell
curl -X PUT http://localhost:8000/cache/user:101 `
  -H "Content-Type: application/json" `
  -d '{"value":{"name":"Gaurav","age":21},"ttl":300}'
```

Response shows which nodes received the copy:

```json
{
    "status": "stored",
    "key": "user:101",
    "replicated_to": 2,
    "nodes": ["http://localhost:8001", "http://localhost:8002"]
}
```

### GET (tries primary, falls back to replica)

```powershell
curl http://localhost:8000/cache/user:101
```

### DELETE (invalidates both primary and replica)

```powershell
curl -X DELETE http://localhost:8000/cache/user:101
```

### Live fault-tolerance demo

1. Store a key:

```powershell
curl -X PUT http://localhost:8000/cache/user:101 `
  -H "Content-Type: application/json" `
  -d '{"value":"Gaurav","ttl":300}'
```

2. **Kill Node 2** (`Ctrl+C` in its terminal).

3. The router detects the failure after ~6 seconds (3 checks x 2s):

```
[HEALTH FAIL] http://localhost:8002 (1/3)
[HEALTH FAIL] http://localhost:8002 (2/3)
[HEALTH FAIL] http://localhost:8002 (3/3)
[NODE FAILED] http://localhost:8002
```

> Timing note: a health pass takes `health_interval` plus the time
> the checks themselves take. If your firewall drops connection
> attempts instead of refusing them, each dead-node check costs the
> full 1-second timeout (2s when `localhost` resolves to both IPv4
> and IPv6), so detection can take noticeably longer than 6s.

4. **GET still works** via the replica:

```powershell
curl http://localhost:8000/cache/user:101
# => "Gaurav"
```

5. **Restart Node 2**:

```powershell
python main.py --port 8002 --capacity 100
```

6. Router detects recovery — note the gate: a node that comes back
   after a failure does NOT rejoin immediately. It must prove itself
   through `RECOVERING` first (2 healthy checks by default):

```
[NODE FAILED] http://localhost:8002       <- while it was down
[NODE RECOVERING] http://localhost:8002   <- first healthy check after restart
[NODE READY] http://localhost:8002        <- 2 healthy checks later
```

7. Node 2 rejoins the ring. Check:

```powershell
curl http://localhost:8000/cluster/status
```

> **Note:** When a node rejoins, its local cache is empty.
> The database remains the source of truth (Layer 5).

---

## Layer 5 — Database Integration & Cache-Aside

The router now sits in front of both the cache cluster and a
SQLite database. Cache nodes stay "dumb" — they never see the DB.

```
Client
  │
  ▼
Router :8000
  │
  ▼
CacheService
  │
  ├──► Distributed Cache (Node 1/2/3)
  │
  └──► SQLite  data/cache.db   (source of truth)
```

GET flow: cache HIT → return; cache MISS → database → populate cache →
return. SET writes to database **then** cache. DELETE removes from
database **and** invalidates the cache.

Start the nodes and router exactly as in Layer 4 (4 terminals), then
try the commands below.

### PUT — writes to database + cache

```powershell
curl -X PUT http://localhost:8000/cache/user:42 `
  -H "Content-Type: application/json" `
  -d '{"value":{"name":"Gaurav","age":20},"ttl":300}'
```

### GET — served from cache

```powershell
curl http://localhost:8000/cache/user:42
```

```json
{
    "key": "user:42",
    "value": { "name": "Gaurav", "age": 20 },
    "source": "cache",
    "node": "http://localhost:8002"
}
```

### Simulate a cache miss (database fallback)

Delete the key from the cache nodes directly, leaving SQLite intact.
The simplest way is to restart the nodes, or hit each node's
`/cache/{key}` DELETE endpoint on ports 8001-8003. Then:

```powershell
curl http://localhost:8000/cache/user:42
```

```json
{
    "key": "user:42",
    "value": { "name": "Gaurav", "age": 20 },
    "source": "database",
    "node": null
}
```

Request it once more — it is now back in the cache (`source: "cache"`).

### Direct database endpoints (TEMPORARY — for testing only)

```powershell
# Read straight from SQLite (bypasses the cache)
curl http://localhost:8000/db/user:42

# Delete straight from SQLite (cache is NOT touched)
curl -X DELETE http://localhost:8000/db/user:42
```

> These `/db/*` endpoints exist only to make the cache-aside behavior
> observable while developing. They are not part of the final design.

### Layer 5 tests

```powershell
pytest tests/test_repository.py tests/test_cache_service.py -v
pytest tests/test_router.py -v
```

- `test_repository.py` — SQLite CRUD + JSON round-trip.
- `test_cache_service.py` — cache-aside logic + stampede protection
  (5 concurrent misses trigger a single database read) + DB-failure tests.
- `test_circuit_breaker.py` — CLOSED/OPEN/HALF-OPEN state machine.
- `test_single_flight.py` — leader/follower request coalescing.
- `test_router.py` — includes Layer 5 end-to-end tests for database
  fallback and the `/db/*` endpoints.

---

## Layer 5.7 — Database Failure Handling (Circuit Breaker)

The router distinguishes **404** (key missing in cache AND database) from
**503** (database unreachable). A `CircuitBreaker` sits between the cache
service and the database:

```
CLOSED  --(3 consecutive failures)-->  OPEN
  ▲                                        │
  │                                  (30s cooldown)
  └------(probe success)------------ HALF-OPEN
```

- CLOSED: every DB call passes through and is counted.
- OPEN: calls are rejected immediately (fast fail, no DB hammering).
- HALF-OPEN: one probe call is allowed; success closes the circuit,
  failure re-opens it.

While the circuit is OPEN, cache **hits are still served normally** —
only cache misses cannot be satisfied.

### Inspect the breaker

```powershell
curl http://localhost:8000/circuit-breaker/status
```

```json
{
    "state": "closed",
    "failure_count": 0,
    "success_count": 0,
    "failure_threshold": 3,
    "recovery_timeout": 30,
    "success_threshold": 1
}
```

### Live demo

1. Start the 3 nodes + router (as in Layer 4).
2. Delete/corrupt the SQLite file so it cannot open:

```powershell
Stop-Service -NoWait  # or simply move data/cache.db away
Move-Item data\cache.db data\cache.db.bak
```

3. A cache miss now returns **503**:

```powershell
curl http://localhost:8000/cache/user:404
```

4. After 3 failures the breaker is OPEN and the router answers fast:

```powershell
curl http://localhost:8000/circuit-breaker/status
# state: "open"
```

5. Restore the DB and wait ~30s for HALF-OPEN → a successful probe
   returns the breaker to CLOSED.

> SET and DELETE are protected too: writes through a dead database
> return 503 instead of a raw server error.

---

## Layer 5.8 — Cache Stampede Protection (Single-Flight)

Concurrent GETs for the same key that miss the cache all collapse into
**one** database read:

```
GET user:42  x 5  (all miss the cache)
        │
        ▼
   SingleFlight("user:42")
        │
        ├── 1 leader  ──►  database read   (one query)
        │
        └── 4 followers ──►  wait on Event, reuse leader's result
```

- Followers never re-query the database.
- If the leader FAILS (e.g. DB down), the exception is stored and
  re-raised in every follower — nobody retries the dead database.
- Different keys are independent (per-key flight).
- Single-flight is process-local, so it is per-router-process.

Verified by `test_single_flight.py` and the existing
`test_concurrent_misses_hit_database_only_once` in
`tests/test_cache_service.py`.

---

## Layer 6.1 — Node Lifecycle Management

Layer 4 only knew UP / DOWN. Layer 6 needs more: a node that has
just (re)started is reachable but its RAM is empty — sending it
normal traffic would turn every cache hit into a database miss.

The router therefore tracks a lifecycle state per node:

```
                     register (router boot)
                             │
                         ┌───▼────┐
         3 failures ┌───►│STARTING│
         ───────────┤    └───┬────┘
                    │        │ first healthy check
                    │        ▼
                    │   ┌──────────┐
         3 failures ├──►│RECOVERING│  gated: NOT in the ring
         ───────────┤   └───┬──────┘
                    │        │ recovery_threshold healthy checks
                    │        ▼
                    │   ┌───────┐
                    ├──►│ READY │──── in the ring, takes traffic
                    │   └───┬───┘
                    │        │ first failed check
                    │        ▼
                    │   ┌───────────┐
         3 failures ├──►│ UNHEALTHY │── healthy ──► back to READY
         ───────────┤   └─────┬─────┘
                    │         │
                    ▼         ▼
               ┌────────────────────┐
               │       FAILED       │  evicted from the ring
               └─────────┬──────────┘
                         │ healthy: gated rejoin
                         ├──────────► RECOVERING
                         │ start_node(): explicit restart
                         └──────────► STARTING
```

Rules:

- **Only `ready` and `unhealthy` nodes are in the consistent-hash
  ring.** `starting`, `recovering` and `failed` nodes are invisible
  to routing.
- `unhealthy` = transient blip (failures below `failure_threshold`):
  the node stays routable, exactly like Layer 4 behaved.
- `failed` = `failure_threshold` (default 3) consecutive failures:
  the node is evicted from the ring.
- **Gated rejoin:** a `failed` node that becomes healthy again goes
  to `recovering`, NOT straight back to `ready`. It must pass
  `recovery_threshold` (default 2) consecutive healthy checks
  before traffic is routed to it again.
- A node flapping during `recovering` has its recovery count reset.

### Named lifecycle operations

`NodeManager` exposes the state machine through five named
operations (public API over `node_lifecycle.enter_state`):

| Method | Effect |
|---|---|
| `start_node(node)` | Unknown node → `starting` (gated). `failed` node → `starting` (explicit restart, failure counters reset). Otherwise no-op. |
| `mark_recovering(node)` | Gate the node: leaves the ring. Legal from `starting` / `failed`. |
| `mark_ready(node)` | Put the node into the ring. Legal from `recovering` / `unhealthy` — from `failed` it raises `InvalidTransitionError` (the rejoin gate cannot be skipped). |
| `mark_failed(node)` | Evict the node from the ring. Legal from every state. |
| `is_available(node)` | `True` when the node is routable (`ready` + `unhealthy`). |

Illegal jumps raise `InvalidTransitionError`; operations on an
unregistered node raise `ValueError`. The health monitor keeps
driving transitions with the thresholds — these methods are the
manual/API surface over the same machine.

```python
manager.start_node("http://localhost:8004")   # -> starting (gated)
manager.mark_recovering("http://localhost:8004")  # -> recovering
manager.mark_ready("http://localhost:8004")       # -> ready, in ring
manager.is_available("http://localhost:8004")     # -> True
```

### Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `RECOVERY_THRESHOLD` | `2` | Healthy checks required in `recovering` before `ready` |
| `CLUSTER_BOOTSTRAP_STATE` | `starting` | Initial state of declared nodes at router boot |

Set `CLUSTER_BOOTSTRAP_STATE=ready` to restore the old Layer 4
behavior (nodes trusted immediately at boot).

Tests and demos call `configure_cluster(...)`, which bootstraps as
`ready` so they can route traffic without waiting for the monitor.

### Inspect the lifecycle

```powershell
curl http://localhost:8000/cluster/status
```

```json
{
    "total_nodes": 3,
    "active_nodes": ["http://localhost:8001", "http://localhost:8002"],
    "inactive_nodes": ["http://localhost:8003"],
    "node_states": {
        "http://localhost:8001": "ready",
        "http://localhost:8002": "unhealthy",
        "http://localhost:8003": "recovering"
    },
    "recovering_nodes": ["http://localhost:8003"],
    "failed_nodes": [],
    "replication_factor": 2,
    "health_interval": 2,
    "failure_threshold": 3,
    "recovery_threshold": 2
}
```

### Live demo (gated rejoin)

Start 3 nodes + router as in Layer 4, wait until all three show up
under `active_nodes`, then:

1. Store a key and kill Node 2 (`Ctrl+C` in its terminal).

2. Watch the router log (Layer 4 demo above):
   `[HEALTH FAIL] ... (3/3)` → `[NODE FAILED] http://localhost:8002`.

3. `GET` still works through the replica.

4. Restart Node 2:

```powershell
python main.py --port 8002 --capacity 100
```

5. The router logs `[NODE RECOVERING] http://localhost:8002` on the
   first healthy check — the node is reachable but still gated out
   of the ring. Only after `recovery_threshold` more healthy checks
   does it log `[NODE READY]` and rejoin.

6. Verify:

```powershell
curl http://localhost:8000/cluster/status
```

### Layer 6.1 tests

```powershell
pytest tests/test_node_lifecycle.py -v
```

- Bootstrap gating (`starting` nodes are not routable)
- `STARTING → RECOVERING → READY` timing against `recovery_threshold`
- `READY → UNHEALTHY → READY` blip tolerance (node stays in ring)
- `READY → FAILED` eviction at `failure_threshold`
- Gated rejoin: `FAILED → RECOVERING → READY` (never straight back)
- Recovery count reset on flapping
- Invalid transitions raise `InvalidTransitionError`
- Named operations: `start_node` / `mark_recovering` / `mark_ready` /
  `mark_failed` / `is_available` (incl. the `FAILED -> READY` block)
- `get_status()` lifecycle fields
- Health pass with an injected checker (no HTTP)
- Concurrency: ring membership always matches state

---

## Layer 6.2 — Cache Statistics & Hot-Key Tracking

Layer 6.1 decides *which nodes* get traffic. Layer 6.2 makes the
cache measure *what the traffic actually uses* — the raw material
for the Layer 6.3/6.4 warm-up.

Each cache node process owns one `CacheStatistics` object:

```
GET  ──► record_hit(key) / record_miss(key)   ──┐
SET  ──► record_set()                          ──┤  all in memory,
DEL  ──► record_delete()                       ──┤  never the DB
LRU  ──► record_eviction(key)  (on_eviction)   ──┘  (on_eviction hook)
```

Every operation increments counters and (for GETs) bumps
`key_access[key]`, so the node can rank keys:

```
user:42       → 50,231 accesses
product:10    → 42,819 accesses
user:91       → 31,452 accesses
```

Two properties keep this from becoming its own problem:

- **Bounded tracking** — `key_access` holds at most
  `max_tracked_keys` (default 100,000) entries. Once full,
  brand-new keys are not tracked (they land in `untracked`),
  known keys keep counting, and the global counters always count.
- **5-minute windows** — counters describe the *current* window
  only. A background thread rotates every `window_seconds`
  (default 300): the finished window is stored as `last_window`
  and everything resets, so "hot" always means *recently* hot.

Hit rate follows the plain definition:

```
hit_rate = hits / (hits + misses)        # 0.92 == 92%
```

### Endpoints (per node, ports 8001-8003)

```powershell
# Current window: counters + hit rate + hottest keys + last_window
curl "http://localhost:8001/stats/window?limit=5"
```

```json
{
    "hits": 9200,
    "misses": 800,
    "sets": 750,
    "deletes": 50,
    "evictions": 100,
    "total_gets": 10000,
    "hit_rate": 0.92,
    "untracked": 0,
    "tracked_keys": 342,
    "max_tracked_keys": 100000,
    "window_seconds": 300,
    "window_started_at": 1730000000.0,
    "last_window": null,
    "top_keys": [["user:42", 5000], ["product:10", 3200]]
}
```

```powershell
# Just the ranking
curl "http://localhost:8001/stats/top-keys?limit=10"
```

```json
{
    "top_keys": [["user:42", 5000], ["product:10", 3200]],
    "tracked_keys": 342,
    "window_seconds": 300
}
```

The Layer 1 `GET /stats` endpoint is unchanged (capacity / size /
LRU hit percentage).

> **Local statistics.** These numbers are per node. `GET user:42`
> hitting Node 2 is invisible to Nodes 1 and 3 — which is exactly
> right, because with consistent hashing only the owner sees the
> traffic. Cluster-level aggregation across nodes arrives with
> Layer 6.3, which needs it to compute a cluster-wide hot list
> for warm-up.

### Live demo

```powershell
# Generate some traffic against a running node (Layer 4 setup)
curl -X PUT http://localhost:8001/cache/user:42 `
  -H "Content-Type: application/json" `
  -d '{"value":"Gaurav","ttl":300}'

curl http://localhost:8001/cache/user:42     # hit
curl http://localhost:8001/cache/nope        # 404 -> miss

curl "http://localhost:8001/stats/top-keys?limit=5"
```

### Configuration

Constructor defaults used by `main.py`:

| Setting | Default | Meaning |
|---|---|---|
| `max_tracked_keys` | `100000` | Bound on `key_access` entries |
| `window_seconds` | `300` | Automatic window rotation period |
| `auto_rotate` | off in tests / on via `start()` | Background rotation thread |

`python main.py` starts the rotation thread automatically; when the
module is imported (tests), no thread is started.

### Layer 6.2 tests

```powershell
pytest tests/test_statistics.py -v
```

- Counter recording + `hit_rate` (incl. zero-GET edge)
- Misses count toward key access (they were requested)
- Hot-key ranking via `top_keys()`
- Bounded tracking: new keys dropped at the cap, known keys and
  global counters keep counting (`untracked`)
- Window rotation: reset + `last_window` snapshot
- Automatic rotation thread (start / stop / daemon)
- Concurrency: 4 threads × repeated events stay consistent
- `LRUCache` eviction hook receives the evicted key
- Node HTTP endpoints: `/stats/window`, `/stats/top-keys`,
  and unchanged `/stats`

---

## Layer 6.3 — Hot-Key Aggregation & Cache Warm-Up

When a cache node restarts its memory is empty. Layer 6.3 uses the
Layer 6.2 statistics to proactively reload the most important keys
back into that node **before** it is allowed to serve normal traffic:

```
Node restarts
     │
     ▼
  STARTING ──health check──► RECOVERING   (gated: not in the ring)
                                  │
                                  ▼
                    hot keys (router view + node stats)
                                  │
                                  ▼
                 ownership filter (ring view WITH this node)
                                  │
                                  ▼
                          rate limiter (100 DB reads/s)
                                  │
                                  ▼
                     database (source of truth; missing = skip)
                                  │
                                  ▼
                   direct write to the recovering node
                                  │
                                  ▼
                     warm-up done + recovery_threshold
                                  │
                                  ▼
                               READY ──► rejoin the ring
```

### Who knows what is hot?

Two sources are combined:

- **Router-side `HotKeyManager`** (`cache/hot_keys.py`) — every key
  the application touches through the router (GET/SET/DELETE) is
  recorded here. Because it lives in the router process it *survives
  node restarts* — exactly when warm-up needs it.
- **Node-local Layer 6.2 windows** — before each warm-up (and on
  each `/cluster/hot-keys` call) the router fans out to every active
  node's `/stats/top-keys` and folds the rankings in, so traffic
  that bypassed the router is covered too. Merged counts are
  additive; only the order matters.

The ranking is bounded (`HOTKEY_MAX_KEYS`, default 10,000).

### Ownership (Section 10 corrected)

A RECOVERING node is gated **out of the live ring**, so
`ring.get_node(key)` can never return it. Warm-up therefore computes
ownership on a ring *view* that includes the node
(`ConsistentHashRing.with_node` / `NodeManager.expected_primary`) and
warms only the keys for which it would be the **primary**. Keys where
it is merely a replica come back later via Layer 7.6 re-replication;
the database remains the correctness source meanwhile.

### Recovery gate (Layer 6.4)

`RECOVERING → READY` now requires the warm-up record to be
`done` (or warm-up disabled) **in addition to** the recovery
threshold of healthy checks. A node cannot rejoin the hash ring
before its recovery requirements are met — 6.4 "not routed until
recovery complete" is satisfied by construction.

**Database failure during warm-up** (Section 16): the attempt is
marked `failed`, the node **stays RECOVERING**, and the health loop
retries the warm-up on every pass until the database returns.
`FAILED` stays reserved for actual health-check failures. Reaching
the top-`WARMUP_LIMIT` keys counts as recovered (Section 17) —
everything else loads lazily on cache MISS.

### Endpoints (router, port 8000)

```powershell
# Merged cluster hot-key ranking (+ per-node window summaries)
curl "http://localhost:8000/cluster/hot-keys?limit=10&per_node=100"

# Warm-up state per node
curl http://localhost:8000/cluster/warmup

# Manually (re)start a warm-up for one node
curl -X POST "http://localhost:8000/cluster/warmup/http%3A%2F%2Flocalhost%3A8001"
```

```json
{
    "top_keys": [["user:42", 5000], ["product:17", 4200]],
    "tracked_keys": 342,
    "max_keys": 10000,
    "untracked": 0,
    "nodes_queried": 3,
    "per_node": {
        "http://localhost:8001": {
            "top_keys": [["user:42", 1200]],
            "tracked_keys": 140
        }
    }
}
```

```json
{
    "enabled": true,
    "limit": 1000,
    "rate_limit": 100,
    "hot_keys": {"tracked_keys": 342, "max_keys": 10000, "untracked": 0},
    "nodes": {
        "http://localhost:8001": {
            "state": "done",
            "attempts": 1,
            "keys_attempted": 4,
            "keys_loaded": 3,
            "keys_not_owned": 96,
            "keys_missing": 1,
            "keys_write_failed": 0,
            "duration": 0.04,
            "error": null
        }
    }
}
```

Warm-up states: `pending → running → done | failed` (plus
`disabled`). A manual trigger on an unknown node returns 404.

### Configuration

| Env var | Default | Meaning |
|---|---|---|
| `WARMUP_ENABLED` | `on` | Master switch (off = ungated READY) |
| `WARMUP_LIMIT` | `1000` | Top-K target — reaching it = recovered |
| `WARMUP_RATE_LIMIT` | `100` | Max database reads/second during warm-up |
| `HOTKEY_MAX_KEYS` | `10000` | Bound on the router-side ranking |

```powershell
$env:WARMUP_LIMIT = "200"
$env:WARMUP_RATE_LIMIT = "50"
uvicorn router.router:app --port 8000
```

### Live demo (with the Layer 4 setup running)

```powershell
# 1. Generate traffic through the router (feeds both views)
1..10 | ForEach-Object {
  curl -X PUT http://localhost:8000/cache/user:42 `
    -H "Content-Type: application/json" -d '{"value":"Gaurav","ttl":300}'
  curl http://localhost:8000/cache/user:42 | Out-Null
}

# 2. Inspect the merged hot list
curl "http://localhost:8000/cluster/hot-keys?limit=5"

# 3. Kill a node (Ctrl+C its terminal), then restart it:
python main.py --port 8001

# 4. Watch it recover: state=running -> done, then READY
curl http://localhost:8000/cluster/warmup
curl http://localhost:8000/cluster/status

# 5. Or drive a warm-up manually for any node
curl -X POST "http://localhost:8000/cluster/warmup/http%3A%2F%2Flocalhost%3A8002"
```

On a **cold boot** (no statistics yet) warm-up completes instantly
with zero keys and the node goes READY right away — warm-up is an
optimization, never a blocker (Section 18).

### Layer 6.3 tests

```powershell
pytest tests/test_warmup.py -v
```

- `HotKeyManager`: ranking, record bound (6.2 policy), merge
  (additive + trim to bound)
- `CacheService` records every proxied key into the ranking
- Rate limiter: spacing, unlimited mode, negative rejected
- Ownership: only keys the recovering node would own are warmed
  (regression: the live ring never returns a gated node)
- Warm-up work: DB → direct write, stale stats (deleted keys)
  skipped, node statistics merged before warming
- DB failure → `failed` + gate blocks READY → health-pass retry →
  eventual READY (Section 16)
- No gate configured → plain 6.1 rejoin still works
- Failed writes still complete the attempt (health monitor owns
  node failure, Section 18)
- Listeners fire after the lock is released; listener errors are
  isolated
- Explicit restart (→ STARTING) forgets the old warm-up record
- Double `ensure()` dedupes; disabled mode gates nothing
- Endpoints: `/cluster/hot-keys` (merge + shape),
  `/cluster/warmup` (status + manual trigger + 404)

---

## Generating a synthetic dataset

No Kaggle account or downloaded file needed — `generate_dataset.py`
writes ready-to-import datasets in all three supported formats:

```powershell
# All three datasets, 1000 records each (deterministic: seed 42)
python generate_dataset.py --kind all --count 1000

# Just one dataset, larger, different seed
python generate_dataset.py --kind users --count 5000 --seed 7
```

| Kind | File | Format | Key column | Suggested prefix |
|---|---|---|---|---|
| `users` | `datasets/users.csv` | CSV | `id` | `user:` |
| `products` | `datasets/products.json` | JSON | `sku` | `product:` |
| `orders` | `datasets/orders.jsonl` | JSONL | `order_id` | `order:` |

The same `--seed` always produces byte-identical files, so
re-generating never changes existing rows on re-import.

Import what it generated (the script prints these commands too):

```powershell
python import_dataset.py --file datasets/users.csv     --key-column id      --prefix user:
python import_dataset.py --file datasets/products.json --key-column sku     --prefix product:
python import_dataset.py --file datasets/orders.jsonl  --key-column order_id --prefix order:
```

Verify what landed in the database:

```powershell
python -c "from database.repository import DatabaseRepository as R; r = R(); print('rows:', r.count()); print(r.keys(limit=5))"
```

```powershell
rows: 3000
['order:o-000001', 'order:o-000002', 'order:o-000003', 'order:o-000004', 'order:o-000005']
```

`count()` and `keys(limit)` are small read-only helpers on
`DatabaseRepository` for exactly this kind of sanity check.

Then exercise the cache-aside path against a running cluster
(Layer 4 setup): a first `GET` misses the cache and reads SQLite,
the second is served from the cache:

```powershell
curl http://localhost:8000/cache/user:42          # source: "database"
curl http://localhost:8000/cache/user:42          # source: "cache"
curl http://localhost:8000/db/product:p-000007    # straight from SQLite
curl http://localhost:8000/db/order:o-000007       # straight from SQLite
```

### Generator tests

```powershell
pytest tests/test_generate_dataset.py tests/test_repository.py -v
```

---

## Loading a dataset (CSV / JSON / JSONL)

Any tabular dataset (Kaggle, OpenML, data.gov, ...) can be loaded
into the SQLite source of truth with `import_dataset.py`. Each
record becomes one `cache_data` row:

```
key   = --prefix + <value of the key column>
value = the rest of the record, stored as JSON
```

```powershell
# CSV — 'id' is detected automatically as the key column
python import_dataset.py --file users.csv --prefix user:

# Pick the key column yourself
python import_dataset.py --file products.json --key-column sku --prefix product:

# JSONL (one JSON object per line)
python import_dataset.py --file logs.jsonl --key-column request_id --prefix log:

# Validate without writing anything
python import_dataset.py --file users.csv --dry-run

# Import only the first 1000 rows, replacing what is already there
python import_dataset.py --file users.csv --prefix user: --limit 1000 --clear

# Store only some columns
python import_dataset.py --file users.csv --value-columns name,age
```

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--file` | (required) | `.csv`, `.tsv`, `.json`, `.jsonl`, `.ndjson` |
| `--key-column` | `id`, else first column | Column that builds the cache key |
| `--prefix` | empty | Prepended to every key (`user:`) |
| `--value-columns` | all except key | Comma-separated subset to store |
| `--limit` | none | Import at most N records |
| `--db` | `data/cache.db` | SQLite path (`CACHE_DB_PATH` respected) |
| `--batch-size` | 500 | Rows per transaction |
| `--clear` | off | Delete existing rows first |
| `--dry-run` | off | Parse and validate, write nothing |

Notes:

- CSV cells are auto-typed: `"21"` becomes the number `21`.
- Duplicate keys overwrite (last record wins) and are reported.
- Rows are written in batches, so importing 100k records takes
  seconds, not minutes.
- After importing, the data flows through the normal cache-aside
  path: a `GET` misses the cache, reads SQLite, and populates the
  cache.

```powershell
python import_dataset.py --file users.csv --prefix user: --db data/cache.db
curl http://localhost:8000/cache/user:42
```

---

## Full test run

```powershell
pytest -v
```

Expected: **170 tests pass** — 14 cache + 16 consistent hash +
8 router + 15 repository + 13 cache-service + 11 circuit-breaker +
5 single-flight + 28 node lifecycle + 14 dataset import +
15 statistics + 21 warm-up + 10 dataset generator.

---

## Stopping everything

In each terminal press `Ctrl + C`.

---

## Notes

- Tests are hermetic: `tests/conftest.py` points `CACHE_DB_PATH` at a
  throwaway file, so `pytest` never reads or clears the real
  `data/cache.db` (the router tests used to call `database.clear()`
  against it).
- The router runs a background health-check thread every 2 seconds.
  A node is marked FAILED after **3 consecutive failures** and
  evicted from the hash ring. A recovered node must then sit through
  RECOVERING for **2 healthy checks** before it is routable again
  (Layer 6.1).
- Declared nodes boot as `starting` (gated) — see the Layer 4
  startup note. Override with `CLUSTER_BOOTSTRAP_STATE=ready`.
- Each PUT writes to **2 nodes** (replication factor = 2).
  Each GET tries primary first, then falls back to the replica.
- When a node crashes, its in-memory cache is lost.
  Layer 5 repopulates it from the database on the next cache miss.
- Cache nodes measure their own traffic in memory (Layer 6.2):
  per-node hit/miss counters and a bounded, 5-minute sliding list
  of hot keys, exposed through `/stats/window` and
  `/stats/top-keys`. Nothing is ever written to the database just
  to measure cache usage.
- The router keeps its own cluster-wide hot-key ranking (Layer 6.3)
  and uses it to warm a restarted node before the node re-enters the
  hash ring. Warm-up is throttled (`WARMUP_RATE_LIMIT`) and never
  blocks on a missing key — the database stays the source of truth.
  A database outage during warm-up keeps the node in RECOVERING
  (retried every health pass) instead of routing to a cold node.
- Layer 5 uses SQLite at `data/cache.db` (no TTL — the DB keeps data
  forever; TTL only affects the in-memory cache). Override the path
  with the `CACHE_DB_PATH` environment variable.
- To point the router at a different set of nodes, set the
  `CACHE_NODES` environment variable before starting it:
  ```powershell
  $env:CACHE_NODES = "http://localhost:8001,http://localhost:8002"
  uvicorn router.router:app --port 8000
  ```
