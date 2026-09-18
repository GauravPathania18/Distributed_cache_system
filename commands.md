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
├── cluster/              <- Layer 4: cluster management
│   ├── __init__.py
│   ├── consistent_hash.py   <- consistent hashing + virtual nodes
│   └── node_manager.py      <- health checks + failure detection
├── router/               <- Layers 4-5: replication-aware + cache-aside router
│   ├── __init__.py
│   └── router.py
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
│   └── test_single_flight.py
├── data/                 <- generated SQLite DB (gitignored)
│   └── cache.db
├── main.py               <- Layer 2: cache node server
├── demo.py               <- Layer 1 demo
├── demo_hash.py          <- Layer 3.1 demo
├── demo_router.py        <- Layer 3.2 demo
├── requirements.txt
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
    "replication_factor": 2,
    "health_interval": 2,
    "failure_threshold": 3
}
```

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
[NODE DOWN] http://localhost:8002
```

4. **GET still works** via the replica:

```powershell
curl http://localhost:8000/cache/user:101
# => "Gaurav"
```

5. **Restart Node 2**:

```powershell
python main.py --port 8002 --capacity 100
```

6. Router detects recovery:

```
[NODE UP] http://localhost:8002
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

## Full test run

```powershell
pytest -v
```

Expected: **74 tests pass** — 14 cache + 16 consistent hash +
8 router + 7 repository + 13 cache-service + 11 circuit-breaker +
5 single-flight.

---

## Stopping everything

In each terminal press `Ctrl + C`.

---

## Notes

- The router runs a background health-check thread every 2 seconds.
  A node is marked DOWN after **3 consecutive failures** (~6 seconds).
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
