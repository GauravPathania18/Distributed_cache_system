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
├── cache/                <- Layer 1: LRU cache engine
│   ├── __init__.py
│   ├── lru_cache.py
│   └── node.py
├── cluster/              <- Layer 4 + 6.1: cluster management
│   ├── __init__.py
│   ├── consistent_hash.py   <- consistent hashing + virtual nodes
│   ├── health_monitor.py    <- periodic checks -> lifecycle driver
│   ├── node_manager.py      <- membership, states, ring
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
├── services/             <- Layer 5: cache-aside orchestration
│   ├── __init__.py
│   ├── cache_service.py      <- cache-aside policy + stampede protection
│   ├── circuit_breaker.py    <- CLOSED/OPEN/HALF-OPEN DB failure protection
│   ├── distributed_cache.py  <- data-plane client over the cluster
│   └── single_flight.py      <- request coalescing (leader/follower)
├── tests/                <- test suite
│   ├── test_cache.py
│   ├── test_consistent_hash.py
│   ├── test_router.py
│   ├── test_repository.py
│   ├── test_cache_service.py
│   ├── test_circuit_breaker.py
│   ├── test_single_flight.py
│   ├── test_node_lifecycle.py
│   └── test_import_dataset.py
├── data/                 <- generated SQLite DB (gitignored)
│   └── cache.db
├── main.py               <- Layer 2: cache node server
├── import_dataset.py     <- load CSV/JSON/JSONL datasets into SQLite
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
                        └──────────► RECOVERING
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
- `get_status()` lifecycle fields
- Health pass with an injected checker (no HTTP)
- Concurrency: ring membership always matches state

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

Expected: **107 tests pass** — 14 cache + 16 consistent hash +
8 router + 10 repository + 13 cache-service + 11 circuit-breaker +
5 single-flight + 18 node lifecycle + 12 dataset import.

---

## Stopping everything

In each terminal press `Ctrl + C`.

---

## Notes

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
- Layer 5 uses SQLite at `data/cache.db` (no TTL — the DB keeps data
  forever; TTL only affects the in-memory cache). Override the path
  with the `CACHE_DB_PATH` environment variable.
- To point the router at a different set of nodes, set the
  `CACHE_NODES` environment variable before starting it:
  ```powershell
  $env:CACHE_NODES = "http://localhost:8001,http://localhost:8002"
  uvicorn router.router:app --port 8000
  ```
