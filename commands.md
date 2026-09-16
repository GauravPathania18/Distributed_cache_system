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
├── router/               <- Layer 4: replication-aware router
│   ├── __init__.py
│   └── router.py
├── tests/                <- test suite
│   ├── test_cache.py
│   ├── test_consistent_hash.py
│   └── test_router.py
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

## Full test run

```powershell
pytest -v
```

Expected: **35 tests pass** (14 cache + 16 consistent hash + 5 router).

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
  The database (Layer 5) will repopulate it on the next cache miss.
- To point the router at a different set of nodes, set the
  `CACHE_NODES` environment variable before starting it:
  ```powershell
  $env:CACHE_NODES = "http://localhost:8001,http://localhost:8002"
  uvicorn router.router:app --port 8000
  ```
