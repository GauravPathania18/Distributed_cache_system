# DCS — Team Testing Guide

Step-by-step verification manual for the Distributed Cache System.
Audience: the development team (QA / demo prep / pre-submission checks).
This is NOT end-user documentation — it tells you exactly which
commands to run and what output proves each feature works.

Estimated full pass: 50–65 minutes (most of it waiting for
deliberate fault-injection timers). Quick smoke path at the end:
~8 minutes.

Tested platform: Windows 10/11, PowerShell, Python 3.10+.

---

## 0. Ground rules & pre-flight

Read this section once before every testing session.

### 0.1 One-time environment setup

```powershell
cd E:\OneDrive\Desktop\DCS

# Create the venv only if .venv does not exist yet
python -m venv .venv

# Activate it (re-run in EVERY new terminal)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

You must see `(venv)` in the prompt before running anything below.
If activation is blocked by execution policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\activate
```

### 0.2 Rules

| Rule | Why |
|---|---|
| Always activate `.venv` in each new terminal | otherwise `python`/`pytest` may point at a different interpreter |
| Use `curl.exe`, not `curl` | Windows PowerShell aliases `curl` to `Invoke-WebRequest`, which rejects `-X PUT` syntax |
| Use `127.0.0.1` inside scripts/demos | `localhost` can resolve to IPv6 `::1` first on Windows; the firewall drop costs ~2s per request |
| Never start the router with `uvicorn --reload` | the reload watcher kills the background health-monitor thread |
| Never run `pytest` expecting it to touch `data/cache.db` | `tests/conftest.py` redirects tests to a throwaway DB on purpose |
| Ports 8000–8003 must be free before starting | 8000 = router, 8001–8003 = cache nodes |

### 0.3 Pre-flight checks (run before EVERY session)

```powershell
# 1. Ports free? (empty output = good)
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003 `
  -State Listen -ErrorAction SilentlyContinue

# 2. Project DB present with the standard 3000-row dataset?
.venv\Scripts\python -c "from database.repository import DatabaseRepository as R; r=R(); print('rows:', r.count()); print(r.keys(limit=3))"
```

Expected:

```
rows: 3000
['order:o-000001', 'order:o-000002', 'order:o-000003']
```

If `rows: 0` — run the dataset import in Phase 8 first, then come
back. If a port is busy, see Troubleshooting (§11).

### 0.4 How to read this guide

Each phase is independent except where it says "continue with the
cluster from Phase N". Every check shows the command and the
expected output. Anything else = a bug worth reporting.

---

## 1. Phase 0 — Automated test suite

Fastest signal that nothing regressed. No servers needed.

```powershell
.venv\Scripts\activate
pytest -q
```

Expected: **228 passed** in ~25s, no failures, no errors.

Breakdown (for pinpointing a regression):

| Module | Tests | Command to isolate |
|---|---|---|
| LRU cache engine | 14 | `pytest tests/test_cache.py -v` |
| Consistent hashing | 16 | `pytest tests/test_consistent_hash.py -v` |
| Router endpoints | 8 | `pytest tests/test_router.py -v` |
| SQLite repository (+ read-only SQL) | 20 | `pytest tests/test_repository.py -v` |
| Cache-aside service | 13 | `pytest tests/test_cache_service.py -v` |
| Circuit breaker | 11 | `pytest tests/test_circuit_breaker.py -v` |
| Single-flight coalescing | 5 | `pytest tests/test_single_flight.py -v` |
| Node lifecycle (6.1) | 28 | `pytest tests/test_node_lifecycle.py -v` |
| Dataset importer | 14 | `pytest tests/test_import_dataset.py -v` |
| Statistics / hot keys (6.2) | 15 | `pytest tests/test_statistics.py -v` |
| Warm-up (6.3/6.4) | 21 | `pytest tests/test_warmup.py -v` |
| Dataset generator | 10 | `pytest tests/test_generate_dataset.py -v` |
| Node URL config | 6 | `pytest tests/test_config.py -v` |
| Terminal UI logic | 13 | `pytest tests/test_tui.py -v` |
| Replication (7.2-7.6) | 34 | `pytest tests/test_replication.py -v` |

**Pass criteria:** 228 passed, 0 failed. Any other count means a
lost/added test — diff against this table before suspecting the code.

**Data-safety check after the run** (proves tests are hermetic):

```powershell
.venv\Scripts\python -c "from database.repository import DatabaseRepository as R; print('rows:', R().count())"
```

Expected: `rows: 3000` — unchanged by the test suite.

---

## 2. Phase 1 — Layer demos (no servers)

Pure in-process demos. No ports, no DB.

### 2.1 Layer 1 — local LRU engine

```powershell
python demo.py
```

Pass criteria: runs to completion without a traceback; prints a
short walkthrough of set/get, TTL expiry and LRU eviction.

### 2.2 Layer 3 — consistent hashing

```powershell
python demo_hash.py
```

Pass criteria: prints the ring with virtual nodes, then a
key → node mapping table; exits cleanly.

---

## 3. Phase 2 — Single cache node (Layers 1–2)

Terminal A:

```powershell
.venv\Scripts\activate
python main.py --port 8001 --capacity 100
```

Terminal B (all checks):

```powershell
# Health
curl.exe http://127.0.0.1:8001/health
# => {"status":"healthy"}

# SET with a 60s TTL
curl.exe -X PUT http://127.0.0.1:8001/cache/user:101 `
  -H "Content-Type: application/json" `
  -d '{"value":{"name":"Gaurav","age":21},"ttl":60}'
# => {"status":"stored","key":"user:101"}

# GET it back
curl.exe http://127.0.0.1:8001/cache/user:101
# => {"key":"user:101","value":{"name":"Gaurav","age":21}}

# EXISTS
curl.exe http://127.0.0.1:8001/cache/user:101/exists
# => {"key":"user:101","exists":true}

# MISS returns 404
curl.exe http://127.0.0.1:8001/cache/nope
# => HTTP 404 {"detail":"Cache miss"}

# DELETE
curl.exe -X DELETE http://127.0.0.1:8001/cache/user:101
# => {"status":"deleted","key":"user:101"}

# DELETE of a missing key -> 404
curl.exe -X DELETE http://127.0.0.1:8001/cache/user:101
# => HTTP 404 {"detail":"Key not found"}

# Layer 1 stats (capacity / size / hits)
curl.exe http://127.0.0.1:8001/stats

# Layer 6.2 window (counters + hit_rate + top_keys)
curl.exe "http://127.0.0.1:8001/stats/window?limit=5"

# Layer 6.2 ranking only
curl.exe "http://127.0.0.1:8001/stats/top-keys?limit=5"
# => {"top_keys":[...],"tracked_keys":N,"window_seconds":300}
```

Pass criteria: every response matches the inline expectation;
`stats/window` shows hits/misses that reflect the traffic above
(at least 1 hit, 1 miss, 1 set, 2 deletes) and `top_keys` contains
`user:101`.

Interactive API docs also work: open `http://127.0.0.1:8001/docs`
in a browser.

Stop the node with `Ctrl+C` in Terminal A when done, or keep it
running — Phase 4 starts its own three nodes on 8001–8003, so
**stop it** to avoid a port clash.

---

## 4. Phase 3 — Full cluster + Terminal UI (main path)

This is the primary way to run and inspect the whole system.

### 4.1 Start

```powershell
.venv\Scripts\activate
python tui.py
```

Expected within ~10–20s:

1. Four new console windows open (router + 3 nodes) — watch their
   logs live.
2. The TUI prints:

```
  started router  :8000  (own window)
  started node :8001  (own window)
  started node :8002  (own window)
  started node :8003  (own window)

  waiting for router ..OK
  waiting for 3 node(s) to become active ............OK
  active: 3/3
```

3. The control-panel menu appears, header shows
   `cluster 3/3 ready ... spawned windows alive 4/4`.

> Nodes are gated at boot (`starting → recovering → ready`, needs
> 2 healthy checks × 2s). If boot ever stalls >60s, see §11.

### 4.2 Walk through every menu option

Work top to bottom. After each action the menu redraws; the TUI
remembers nothing between options — each is a fresh query.

**Option 1 — Cluster status**

Input: `1`

Expected: JSON with `"total_nodes": 3`, all three URLs under
`active_nodes`, `"node_states"` all `"ready"`,
`"replication_factor": 2`, then a per-node readiness list.

**Option 2 — PUT through the router**

Input: `2`, then key `test:tui`, value `{"hello":"world"}`, TTL
blank (=300).

Expected:

```
  stored {"status": "stored", "key": "test:tui",
          "source": "database+cache", "replicated_to": 2,
          "nodes": ["http://127.0.0.1:8001", "http://127.0.0.1:8002"]}
```

Pass criteria: `replicated_to: 2` (replication factor) and the DB
row count grows by 1 (Phase 8 verification command).

**Option 3 — GET through the router**

Input: `3`, key `test:tui`.

Expected:

```
  source: cache
  node  : http://127.0.0.1:8001      <- one of the two replica nodes

  {
    "hello": "world"
  }
```

Pass criteria: `source: cache` (the value was just written).
A dataset key that was never fetched (e.g. `user:42`) returns
`source: database` on its FIRST GET (cache-aside fallback) and
`source: cache` on the second — that two-GET switch is the
Layer 5 behavior; try it now:

```
  3 → user:42   (expect source: database)
  3 → user:42   (expect source: cache)
```

**Option 4 — DELETE**

Input: `4`, key `test:tui`.

Expected:

```
  deleted {"status": "deleted", "key": "test:tui",
           "deleted_from_database": true,
           "nodes": ["http://127.0.0.1:8001", "http://127.0.0.1:8002"]}
```

Pass criteria: removed from BOTH the database and the replica
cache nodes (the two that held it).

**Option 5 — DB get (bypasses the cache)**

Input: `5`, key `user:42`.

Expected: the raw JSON value from `data/cache.db` (the synthetic
user record: name/email/age/city/...). This endpoint is the
"source of truth" view — it must match what GET eventually
returns.

**Option `b` — restart anything down**

Leave for the fault test (Phase 5); typing `b` with everything
healthy prints that nothing needed starting.

**Option 6 — Key inspector (the flagship query)**

Input: `6`, key `user:42`.

Expected (cold cache):

```
  CACHE - direct node reads for 'user:42'
    :8001   MISS
    :8002   MISS
    :8003   MISS

  DATABASE - source of truth (read-only)
    FOUND   {"name": "Elena Aziz", ...}

  VERDICT  DB-ONLY - source of truth has the key, the cache is cold
```

Now GET `user:42` once (option 3) so it is cached, then inspect
again. Expected: the two replica nodes show `HIT` with the same
JSON, the third shows `MISS`, verdict:

```
  VERDICT  CONSISTENT - 2 caching node(s) match the database
```

Verdict reference (all seven are exercised by the test suite; live
testing normally produces DB-ONLY, CONSISTENT and NOT FOUND):

| Verdict | Meaning |
|---|---|
| `CONSISTENT` | every caching node matches the database |
| `PARTIAL` | some nodes match, some hold stale data |
| `STALE` | cached everywhere but differs from the DB |
| `CACHE-ONLY` | cached, but the DB does not have the key |
| `DB-ONLY` | DB has it, cache is cold |
| `NOT FOUND` | neither cache nor DB |
| `DATABASE UNAVAILABLE` | cannot verify (breaker likely open) |

Inspect a key that exists nowhere (`6` → `nope:1`) → `NOT FOUND`.

**Option 7 — List DB keys / prefixes**

Input: `7`, prefix `user:`, limit blank (=20).

Expected:

```
  1000 key(s) matching 'user:' - showing 20

  key        value bytes
  ------------------------
  user:1     147
  user:10    152
  ...
```

Then `7` with a blank prefix → a per-prefix summary
(`user:` 1000, `product:` 1000, `order:` 1000, total 3000) plus
the first 20 keys.

**Option 8 — SQL console (read-only)**

Input: `8`, SQL `SELECT COUNT(*) FROM cache_data`.

Expected:

```
  COUNT(*)
  ----------
  3000
```

Try a JSON query too:

```sql
SELECT key, json_extract(value,'$.city') AS city
FROM cache_data WHERE key LIKE 'user:%' LIMIT 5;
```

Expected: 5 rows with real city names (Berlin, Mumbai, ...).

Negative checks (must be REJECTED, printed in red):

```sql
DELETE FROM cache_data          -- rejected: not read-only
SELECT 1; DELETE FROM cache_data -- rejected: multi-statement
```

**Option 9 — Statistics, hot keys & warm-up**

Input: `9`.

Expected three blocks:

```
  ROUTER
    hot keys tracked: 2
           6  test:tui          <- DELETE counted too; user:42 from your GETs
           3  user:42
    warm-up: enabled=True limit=1000 rate=100.0/s
      8001: done loaded=0 attempts=1
      ...
    breaker: closed (failures 0/3)

  NODE http://127.0.0.1:8001
    hits=N misses=N hit_rate=... sets=N evictions=0
    tracked_keys=N window=300s
           2  test:tui
           ...
```

Pass criteria: router hot-key list contains the keys you touched,
per-node counters are non-zero, breaker `closed`,
warm-up state `done` for every node.

**Option 0 — Stop everything**

Input: `0`.

Expected:

```
  stopping everything this UI started...
```

All four spawned windows close. Verify:

```powershell
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003 `
  -State Listen -ErrorAction SilentlyContinue
```

Expected: empty (all ports free). DB row count back to 3000
(put+delete of `test:tui` cancels out).

**Done — Phase 3 complete.** Restart the cluster for Phase 4
either with `python tui.py` again, or manually as below.

---

## 5. Phase 4 — Router manually (4 terminals)

Low-level view: you see the router's health-check log directly.

> Alternative: keep using the TUI for Phases 5–7 — every curl
> below has a menu equivalent. The manual setup is preferred here
> because the router window prints `[HEALTH FAIL] / [NODE READY]`
> lines that the fault test needs you to observe.

### 5.1 Start (one command per terminal, with venv active)

```powershell
# Terminal 1
python main.py --port 8001 --capacity 100
# Terminal 2
python main.py --port 8002 --capacity 100
# Terminal 3
python main.py --port 8003 --capacity 100
# Terminal 4 — the router (NO --reload)
uvicorn router.router:app --port 8000
```

### 5.2 Wait for the gate

```powershell
curl.exe http://127.0.0.1:8000/cluster/status
```

Poll until all three nodes are `"ready"` under `node_states` AND
listed in `active_nodes` (≈5–10s after router start). PUTs return
503 until then — that is correct behavior, not a bug.

Also:

```powershell
curl.exe http://127.0.0.1:8000/health
# => {"status":"healthy","service":"router"}
```

### 5.3 Replicated PUT / GET / DELETE

```powershell
# PUT - writes DB + 2 cache nodes
curl.exe -X PUT http://127.0.0.1:8000/cache/user:101 `
  -H "Content-Type: application/json" `
  -d '{"value":{"name":"Gaurav","age":21},"ttl":300}'
# => {"status":"stored","key":"user:101","source":"database+cache",
#     "replicated_to":2,"nodes":["http://127.0.0.1:8001", "http://127.0.0.1:8002"]}

# GET - served from cache
curl.exe http://127.0.0.1:8000/cache/user:101
# => {"key":"user:101","value":{...},"source":"cache","node":"http://127.0.0.1:8001"}

# DELETE - DB + both replicas
curl.exe -X DELETE http://127.0.0.1:8000/cache/user:101
# => {"status":"deleted","key":"user:101","deleted_from_database":true, ...}

# 404 when the key exists nowhere
curl.exe http://127.0.0.1:8000/cache/user:101
# => HTTP 404 {"detail":"Key not found in cache or database"}
```

### 5.4 Cache-aside with dataset keys

```powershell
# 1st GET - miss -> SQLite -> populate
curl.exe http://127.0.0.1:8000/cache/user:42
# => ..."source":"database","node":null

# 2nd GET - now cached
curl.exe http://127.0.0.1:8000/cache/user:42
# => ..."source":"cache","node":"http://127.0.0.1:800x"

# Direct DB endpoints (testing-only, bypass cache)
curl.exe http://127.0.0.1:8000/db/user:42
# => {"key":"user:42","value":{...},"source":"database"}
curl.exe http://127.0.0.1:8000/db/product:p-000007
curl.exe -X DELETE http://127.0.0.1:8000/db/order:o-000007   # deletes DB row ONLY
curl.exe http://127.0.0.1:8000/cache/order:o-000007          # => 404 (gone from DB)
```

> The `/db/*` DELETE intentionally leaves the cache stale — that is
> how you can manufacture a `STALE` verdict for the TUI inspector.
> Re-import the dataset (Phase 8) afterwards to restore the row.

### 5.5 Distribution demo (optional)

```powershell
python demo_router.py
```

Pass criteria: PUTs spread across nodes by consistent hash, GETs
resolve; every key prints a node URL, no ERROR lines.

Leave all 4 terminals running for Phase 5.

---

## 6. Phase 5 — Fault tolerance live test

Continues from Phase 4 (cluster healthy, 3/3 ready).

### 6.1 Seed a key

```powershell
curl.exe -X PUT http://127.0.0.1:8000/cache/user:777 `
  -H "Content-Type: application/json" `
  -d '{"value":"survivor","ttl":300}'
```

Note which two nodes are in `nodes` — those hold the replicas.

### 6.2 Kill Node 2

Press `Ctrl+C` in Terminal 2 (port 8002).

### 6.3 Watch the router detect it (Terminal 4)

Expected within ~6–15s:

```
[HEALTH FAIL] http://127.0.0.1:8002 (1/3)
[HEALTH FAIL] http://127.0.0.1:8002 (2/3)
[HEALTH FAIL] http://127.0.0.1:8002 (3/3)
[NODE FAILED] http://127.0.0.1:8002
```

> Timing: 3 checks × 2s interval. If your firewall DROPS instead of
> REFUSING connections to a dead port, each failed check costs the
> full 1s timeout — detection can take ~20s. Still a pass.

### 6.4 Verify status and data survival

```powershell
curl.exe http://127.0.0.1:8000/cluster/status
```

Expected: `8002` under `failed_nodes`, `active_nodes` has 2 URLs.

```powershell
curl.exe http://127.0.0.1:8000/cache/user:777
```

Expected: still returns `"survivor"` (served by a surviving
replica). **This is the core fault-tolerance proof.**

### 6.5 Restart Node 2 (Terminal 2)

```powershell
python main.py --port 8002 --capacity 100
```

Watch Terminal 4 — expected, NOT immediate rejoin:

```
[NODE RECOVERING] http://127.0.0.1:8002   <- gated, NOT in the ring yet
[NODE READY] http://127.0.0.1:8002        <- ~4s later (2 healthy checks)
```

During RECOVERING the router also runs warm-up for the node
(Phase 7 inspects this in detail).

```powershell
curl.exe http://127.0.0.1:8000/cluster/status
```

Expected: 8002 back under `active_nodes`, state `"ready"`,
`failed_nodes` empty.

Pass criteria: FAIL detection logged → replica GET survived →
gated RECOVERING → READY rejoin. The restarted node's RAM is
empty — its cache repopulates lazily from the DB on the next miss
(also a required observation: GET `user:777` right after rejoin
shows `source: database` once, then `source: cache`).

---

## 7. Phase 6 — Circuit breaker & database failure

Continues from the Phase 4/5 cluster. Breaker starts `closed`.

### 7.1 Baseline

```powershell
curl.exe http://127.0.0.1:8000/circuit-breaker/status
# => {"state":"closed","failure_count":0,"failure_threshold":3,
#     "recovery_timeout":30,...}

# Make sure user:42 is cached (GET twice) so HITs still work later
curl.exe http://127.0.0.1:8000/cache/user:42
```

### 7.2 Take the database away

```powershell
Move-Item data\cache.db data\cache.db.bak
```

If Windows says the file is in use, wait 1–2s and retry (the
repository opens short-lived connections per operation).

### 7.3 Misses fail fast, breaker opens

```powershell
# Three misses -> 3 consecutive DB failures
curl.exe http://127.0.0.1:8000/cache/definitely:missing
curl.exe http://127.0.0.1:8000/cache/definitely:missing2
curl.exe http://127.0.0.1:8000/cache/definitely:missing3
# each => HTTP 503 {"detail":"Database unavailable - try again later"}

curl.exe http://127.0.0.1:8000/circuit-breaker/status
# => {"state":"open","failure_count":3,...}

# A 4th miss now fails INSTANTLY (no DB touch attempted)
curl.exe -w "%{time_total}`n" http://127.0.0.1:8000/cache/definitely:missing4
# time should be well under 0.1s
```

### 7.4 Cache HITs still work while OPEN (critical property)

```powershell
curl.exe http://127.0.0.1:8000/cache/user:42
# => 200, "source":"cache"   <- served despite the open breaker
```

Writes are also protected:

```powershell
curl.exe -X PUT http://127.0.0.1:8000/cache/x:y `
  -H "Content-Type: application/json" -d '{"value":1}'
# => HTTP 503 (DB down, nothing stored)
```

### 7.5 Restore and heal

```powershell
Remove-Item data\cache.db            # sqlite recreated an empty shell
Move-Item data\cache.db.bak data\cache.db
```

Wait >30s (recovery cooldown), then any miss triggers the
HALF-OPEN probe:

```powershell
curl.exe http://127.0.0.1:8000/cache/user:42

curl.exe http://127.0.0.1:8000/circuit-breaker/status
# => {"state":"closed","failure_count":0,...}
```

Pass criteria: closed → open at 3 failures → HITs still served →
restored DB + cooldown → closed again.

---

## 8. Phase 7 — Hot keys & cache warm-up (Layers 6.2–6.4)

Continues with the running cluster. The point: a restarted node
gets its hottest keys reloaded BEFORE it is allowed to serve.

### 8.1 Generate traffic through the router

```powershell
1..10 | ForEach-Object {
  curl.exe -X PUT http://127.0.0.1:8000/cache/user:42 `
    -H "Content-Type: application/json" -d '{"value":"hot","ttl":300}' | Out-Null
  curl.exe http://127.0.0.1:8000/cache/user:42 | Out-Null
  curl.exe http://127.0.0.1:8000/cache/product:p-000001 | Out-Null
}
```

### 8.2 Cluster hot-key ranking

```powershell
curl.exe "http://127.0.0.1:8000/cluster/hot-keys?limit=5&per_node=100"
```

Expected: `top_keys` led by `user:42` (highest count), a
`per_node` block with each node's own top keys, `nodes_queried: 3`.

Per-node raw view (Layer 6.2):

```powershell
curl.exe "http://127.0.0.1:8001/stats/top-keys?limit=5"
curl.exe "http://127.0.0.1:8001/stats/window?limit=5"
```

### 8.3 Kill a node, restart it, watch warm-up

1. `Ctrl+C` Terminal 1 (node 8001). Wait for `[NODE FAILED]`.
2. Restart: `python main.py --port 8001 --capacity 100`
3. While it is RECOVERING, poll:

```powershell
curl.exe http://127.0.0.1:8000/cluster/warmup
```

Expected shape:

```json
{
  "enabled": true,
  "limit": 1000,
  "rate_limit": 100,
  "nodes": {
    "http://127.0.0.1:8001": {
      "state": "running",        -> then "done"
      "attempts": 1,
      "keys_attempted": 1,
      "keys_loaded": 1,
      "keys_missing": 0,
      "error": null
    }
  }
}
```

4. Final state:

```powershell
curl.exe http://127.0.0.1:8000/cluster/warmup      # state "done"
curl.exe http://127.0.0.1:8000/cluster/status       # 8001 "ready" again
```

5. Manual trigger (works on any node, any time):

```powershell
curl.exe -X POST "http://127.0.0.1:8000/cluster/warmup/http%3A%2F%2F127.0.0.1%3A8001"

# Unknown node -> 404
curl.exe -X POST "http://127.0.0.1:8000/cluster/warmup/http%3A%2F%2F127.0.0.1%3A9999"
```

Pass criteria: hot list ranks `user:42` first → restart → warm-up
`running`→`done` with the hot key loaded → node READY only after
warm-up completes → manual trigger works, unknown node 404.

> Cold-boot note: if the router itself just started (no traffic
> yet), warm-up completes instantly with `keys_attempted: 0` and
> the node goes READY immediately. Warm-up is an optimization and
> must never block recovery — seeing instant-done on a cold
> cluster is CORRECT, not a failure.

---

## 9. Phase 8 — Dataset pipeline

Verifies the generate → import → serve chain end to end.

### 9.1 Generate synthetic datasets (deterministic, seed 42)

```powershell
python generate_dataset.py --kind all --count 1000
```

Expected files (already present in a prepared checkout — the
command overwrites them identically):

```
datasets\users.csv      1000 records, key column: id
datasets\products.json  1000 records, key column: sku
datasets\orders.jsonl   1000 records, key column: order_id
```

Determinism check: run it twice; the second run must not change
file sizes or row counts.

### 9.2 Import (dry-run first)

```powershell
python import_dataset.py --file datasets/users.csv --dry-run
```

Expected: parses and validates, reports 1000 records, writes
NOTHING (row count unchanged).

```powershell
python import_dataset.py --file datasets/users.csv     --key-column id      --prefix user:     --clear
python import_dataset.py --file datasets/products.json --key-column sku     --prefix product:  --clear
python import_dataset.py --file datasets/orders.jsonl  --key-column order_id --prefix order:    --clear
```

(`--clear` makes the step idempotent — re-runnable any time.)

### 9.3 Verify

```powershell
python -c "from database.repository import DatabaseRepository as R; r=R(); print('rows:', r.count()); print(r.keys(limit=5))"
```

Expected:

```
rows: 3000
['order:o-000001', 'order:o-000002', 'order:o-000003', 'order:o-000004', 'order:o-000005']
```

### 9.4 Serve it through the cluster

With the cluster running (restart via `python tui.py` or the
4-terminal setup if you stopped it):

```powershell
curl.exe http://127.0.0.1:8000/cache/user:42          # 1st: source "database"
curl.exe http://127.0.0.1:8000/cache/user:42          # 2nd: source "cache"
curl.exe http://127.0.0.1:8000/db/product:p-000007    # direct DB read
curl.exe http://127.0.0.1:8000/db/order:o-000007
```

Or simply use TUI options 3/5/6/7/8 from Phase 3 — same data.

Import tests are covered by `pytest tests/test_import_dataset.py
tests/test_generate_dataset.py -v` (24 tests).

---

## 10. Phase 9 — Replication & failover (Layer 7)

Verifies the sequence-numbered write path, the replication log,
manual sync and the recorded failover. With the 4-terminal cluster
running (Phase 4):

### 10.1 Replication status

```powershell
curl.exe http://127.0.0.1:8000/cluster/replication
```

Expected shape:

```json
{
  "enabled": true,
  "current_sequence": 0,
  "log_size": 0,
  "log_capacity": 10000,
  "snapshot_threshold": 1000,
  "nodes": { ... },
  "last_failover": null,
  "failovers": [],
  "syncs": {}
}
```

### 10.2 A write carries a sequence and is acknowledged

```powershell
curl.exe -X PUT "http://127.0.0.1:8000/cache/user:77" -H "Content-Type: application/json" -d "{\"value\":\"seq-check\",\"ttl\":60}"
curl.exe http://127.0.0.1:8000/cluster/replication
```

Expected: `current_sequence` increments by 1 (1, 2, 3… as you issue
writes) and every node listed in the PUT response shows
`acked_sequence` equal to `current_sequence`, `behind: 0`.

The value itself is versioned on the node:

```powershell
curl.exe "http://127.0.0.1:8001/internal/entry/user:77"
```

Expected: `{"key":"user:77","exists":true,"value":"seq-check","sequence":<n>}`

### 10.3 Manual resync + 404

```powershell
curl.exe -X POST "http://127.0.0.1:8000/cluster/resync/http%3A%2F%2F127.0.0.1%3A8001"

# Unknown node -> 404
curl.exe -X POST "http://127.0.0.1:8000/cluster/resync/http%3A%2F%2F9999"
```

Expected: first returns `"state":"done"` with `"mode"` either
`incremental` (small gap, log replay) or `snapshot` (never acked /
large gap, DB pull); the unknown node returns HTTP 404.

### 10.4 Kill a node — failover is recorded

1. `Ctrl+C` the terminal running node 8002. Wait for `[NODE FAILED]`.

```powershell
curl.exe http://127.0.0.1:8000/cluster/replication
curl.exe http://127.0.0.1:8000/cluster/status
```

Expected: `last_failover.node` is `http://127.0.0.1:8002`,
`failovers` has one entry (and grows on each failure, capped at 50),
`/cluster/status` shows `active: 2/3`.

Re-replication runs automatically in the background: the keys the
dead node carried are rewritten onto their new owners so redundancy
returns to the configured replication factor. The automated suite
proves this precisely
(`tests/test_replication.py::test_repair_restores_ownership_after_failure`).

2. Restart it: `python main.py --port 8002 --capacity 100` — it
rejoins gated (RECOVERING) as in Phase 7.

Pass criteria: sequence increments per write → acked on the writing
nodes → resync reports `done`, unknown node 404 → kill 8002 records
`last_failover` and `failovers` history.

---

## 11. Master checklist

Copy this table into the session notes and tick every row.

| # | Feature | Phase | Pass criteria |
|---|---|---|---|
| 1 | Test suite green | 0 | 228 passed |
| 2 | Tests are hermetic | 0 | DB rows still 3000 after pytest |
| 3 | Layer 1 demo | 1 | `demo.py` completes |
| 4 | Consistent hash demo | 1 | `demo_hash.py` prints ring + mapping |
| 5 | Node health endpoint | 2 | `{"status":"healthy"}` |
| 6 | Node SET/GET/EXISTS/DELETE | 2 | values round-trip; 404 on miss |
| 7 | Node stats + window + top-keys | 2 | counters reflect traffic |
| 8 | TUI spawns 4 windows + gate | 3 | `active: 3/3` |
| 9 | TUI PUT (replicated_to 2) | 3 | 2 nodes listed |
| 10 | TUI GET source cache | 3 | `source: cache` |
| 11 | Cache-aside fallback | 3 | 1st GET `database`, 2nd `cache` |
| 12 | TUI DELETE | 3 | DB + replicas cleared |
| 13 | TUI DB get | 3 | raw DB value |
| 14 | Key inspector cold | 3 | `DB-ONLY` |
| 15 | Key inspector warm | 3 | `CONSISTENT` |
| 16 | Key inspector absent key | 3 | `NOT FOUND` |
| 17 | Prefix listing | 3 | `1000 key(s) matching 'user:'` |
| 18 | SQL COUNT(*) | 3 | `3000` |
| 19 | SQL json_extract query | 3 | 5 rows with cities |
| 20 | SQL rejects writes | 3 | red error, nothing deleted |
| 21 | Stats/hot-keys/breaker view | 3 | breaker closed, keys listed |
| 22 | TUI clean stop | 3 | option 0 frees all 4 ports |
| 23 | Router /cluster/status gate | 4 | 503 until ready, then 3/3 |
| 24 | Replicated PUT via curl | 4 | `replicated_to: 2` |
| 25 | GET via curl | 4 | `source: cache` |
| 26 | DELETE via curl | 4 | `deleted_from_database: true` |
| 27 | /db/* direct endpoints | 4 | read + delete bypass cache |
| 28 | demo_router.py | 4 | keys spread over nodes |
| 29 | Node failure detection | 5 | `[NODE FAILED]` in ~6–20s |
| 30 | Replica survives kill | 5 | GET returns value while 2/3 |
| 31 | Gated rejoin | 5 | RECOVERING then READY |
| 32 | Empty node refills lazily | 5 | 1st GET after rejoin `database` |
| 33 | Breaker opens at 3 failures | 6 | state `open`, 503s |
| 34 | Breaker fast-fails when open | 6 | <0.1s response |
| 35 | HITs served while open | 6 | 200 `source: cache` |
| 36 | Breaker heals after restore | 6 | closed after cooldown+probe |
| 37 | Writes protected by breaker | 6 | PUT 503 while DB down |
| 38 | Cluster hot-key ranking | 7 | user:42 on top |
| 39 | Warm-up on restart | 7 | `running` → `done` → READY |
| 40 | Manual warm-up trigger | 7 | works; unknown node 404 |
| 41 | Dataset generate | 8 | 3 files, deterministic |
| 42 | Import dry-run | 8 | validates, writes nothing |
| 43 | Import full | 8 | rows: 3000, keys `o-000001…` |
| 44 | Imported data served | 8 | cache-aside works on user:42 |
| 45 | /cluster/replication status | 9 | sequence, nodes, empty failovers |
| 46 | Write bumps sequence + acks | 9 | acked == current, behind 0 |
| 47 | Node entry carries sequence | 9 | `/internal/entry` shows sequence |
| 48 | Manual resync + 404 | 9 | done; unknown node 404 |
| 49 | Failover recorded on kill | 9 | last_failover + history, 2/3 |
| 50 | Replication unit tests | 0 | 34 replication tests pass |

Result: ___/50 passed  |  Date: ______  |  Tester: ______

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pytest`: 229+ or <228 tests | wrong interpreter / stale checkout | activate `.venv`, `git pull`, re-run |
| Port already in use | leftover process from a crashed session | `Get-NetTCPConnection -LocalPort 8000 -State Listen` → note `OwningProcess` → `Stop-Process -Id <pid> -F` |
| TUI stuck at `waiting for 3 node(s)` | a node window failed to start | check the three node windows for tracebacks; `Ctrl+C` the TUI and restart |
| PUT returns 503 right after boot | gated startup — nodes still `starting/recovering` | wait for `/cluster/status` to list them as `ready` (~5s) — expected behavior |
| Every request is slow (~2s) | something talks to `localhost` (IPv6 fallback penalty) | use `127.0.0.1` in scripts/demos |
| `curl` rejects `-X PUT` | PowerShell alias to Invoke-WebRequest | use `curl.exe` |
| TUI opens no windows | console-session limitation (e.g. some remote shells) | run the 4-terminal manual setup (Phase 4) instead |
| `Move-Item data\cache.db` → in use | SQLite connection still open from the last request | wait 1–2s, retry; the repo uses short-lived connections |
| Breaker never returns to `closed` | cooldown not elapsed, or no miss since restore | wait the full 30s, then issue one GET |
| Empty `data\cache.db` after breaker test | SQLite recreated a shell file while the DB was "away" | the restore steps in §7.5 (`Remove-Item` shell first) handle it |
| `activate` blocked | execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Tests fail with DB errors | someone set `CACHE_DB_PATH` globally | unset it: `Remove-Item Env:CACHE_DB_PATH` |

---

## 13. Cleanup

End of session — all of it takes <30s:

```powershell
# 1. Stop the cluster
#    - TUI: option 0 (stops what it spawned)
#    - Manual: Ctrl+C in each of the 4 terminals

# 2. Ports free?
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003 `
  -State Listen -ErrorAction SilentlyContinue
# => empty

# 3. Database intact?
python -c "from database.repository import DatabaseRepository as R; print('rows:', R().count())"
# => rows: 3000  (re-import via Phase 8 if a fault test deleted rows)

# 4. Nothing stray in git
git status --short
# => only your intended changes

# 5. Stray node windows left behind after a crash?
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *main.py*"
# (or close the windows by hand)
```

---

## Quick smoke path (8 minutes)

For a fast "is it basically alive?" check between larger changes:

1. `pytest -q` → 228 passed (Phase 0)
2. `python tui.py` → `active: 3/3` (Phase 3.1)
3. TUI `3` → `user:42` twice → `database` then `cache`
4. TUI `6` → `user:42` → `CONSISTENT`
5. TUI `8` → `SELECT COUNT(*) FROM cache_data` → `3000`
6. TUI `0` → ports free

If those six pass, the core (cluster + cache-aside + DB + TUI)
is healthy; run the full guide before a release or demo.
