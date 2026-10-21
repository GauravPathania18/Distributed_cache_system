# DCS — Team Testing Guide

Step-by-step verification manual for the Distributed Cache System.
Audience: the development team (QA / demo prep / pre-submission checks).
This is NOT end-user documentation — it tells you exactly which
commands to run and what output proves each feature works.

Estimated full pass: 55–70 minutes (most of it waiting for
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

# 2. Datasets imported? (one SQLite file per imported dataset)
.venv\Scripts\python -c "from database.registry import DatasetRegistry as R; print(R().names())"
```

Expected: a non-empty list of your dataset names, e.g.

```
['imdb_movies', 'netflix_titles', 'zomato_restaurant_dataset']
```

If it prints `[]` — drop a file into `datasets/` and run
`python import_dataset.py --seed` (Phase 8), then come back.
If a port is busy, see Troubleshooting (§11).

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

Expected: **303 passed** in ~55s, no failures, no errors.

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
| Dataset importer (files → per-dataset SQLite) | 29 | `pytest tests/test_import_dataset.py -v` |
| Dataset registry (manifest + schema/key sniff) | 19 | `pytest tests/test_registry.py -v` |
| Multi-dataset repository (prefix routing) | 15 | `pytest tests/test_multi_repository.py -v` |
| Statistics / hot keys (6.2) | 15 | `pytest tests/test_statistics.py -v` |
| Warm-up (6.3/6.4) | 21 | `pytest tests/test_warmup.py -v` |
| Node URL config | 6 | `pytest tests/test_config.py -v` |
| Terminal UI logic | 13 | `pytest tests/test_tui.py -v` |
| Replication (7.2-7.6) | 34 | `pytest tests/test_replication.py -v` |
| Cached search (8.1) | 25 | `pytest tests/test_search.py -v` |
| Telemetry / DB-load metric (8.2) | 7 | `pytest tests/test_telemetry.py -v` |
| Dashboard + web UI (8.3) | 4 | `pytest tests/test_dashboard.py -v` |

**Pass criteria:** 303 passed, 0 failed. Any other count means a
lost/added test — diff against this table before suspecting the code.

**Data-safety check after the run** (proves tests are hermetic):

```powershell
.venv\Scripts\python -c "from database.registry import DatasetRegistry as R; print('datasets:', R().names())"
```

Expected: **identical to the pre-run listing** — the suite points
`CACHE_DB_PATH` at a throwaway directory, so it never touches the
real `data/` (system store, dataset files or registry).

---

## 2. Phase 1 — Dataset pipeline smoke (no servers)

Pure in-process checks. No ports, no cluster.

### 2.1 Dry-run an import (parse + plan, writes nothing)

```powershell
python import_dataset.py --file datasets/netflix_titles.csv --dry-run
```

Pass criteria: prints the detected format, columns, key column
(`show_id`) and prefix (`netflix_titles:`), then
`Dry run : nothing was written`.

### 2.2 Registry listing

```powershell
python import_dataset.py --list
```

Pass criteria: one line per registered dataset with live row
counts, prefix and key column; no stack trace.

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

0. Before anything starts, the drop folder seeds (once per file):

```
  imported netflix_titles.csv -> netflix_titles (8807 rows)
  imported imdb_movies.csv -> imdb_movies (10178 rows)
```

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
A dataset key that was never fetched (e.g. `netflix_titles:s1`)
returns `source: database` on its FIRST GET (cache-aside fallback)
and `source: cache` on the second — that two-GET switch is the
Layer 5 behavior; try it now:

```
  3 → netflix_titles:s1   (expect source: database)
  3 → netflix_titles:s1   (expect source: cache)
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

Input: `5`, key `netflix_titles:s1`.

Expected: the raw JSON record from the dataset's own file
`data/datasets/netflix_titles.db` — the imported row
(`title: "Dick Johnson Is Dead"`, type, rating, ...). This endpoint
is the "source of truth" view — it must match what GET eventually
returns.

**Option `b` — restart anything down**

Leave for the fault test (Phase 5); typing `b` with everything
healthy prints that nothing needed starting.

**Option 6 — Key inspector (the flagship query)**

Input: `6`, key `netflix_titles:s1`.

Expected (cold cache):

```
  CACHE - direct node reads for 'netflix_titles:s1'
    :8001   MISS
    :8002   MISS
    :8003   MISS

  DATABASE - source of truth (read-only)
    FOUND   {"type": "Movie", "title": "Dick Johnson Is Dead", ...}

  VERDICT  DB-ONLY - source of truth has the key, the cache is cold
```

Now GET `netflix_titles:s1` once (option 3) so it is cached, then
inspect again. Expected: the two replica nodes show `HIT` with the
same JSON, the third shows `MISS`, verdict:

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

**Option 7 — List keys across every store**

Input: `7`, prefix `netflix_titles:`, limit blank (=20).

Expected:

```
  8807 key(s) matching 'netflix_titles:' - showing 20

  key                        value bytes
  ----------------------------------------
  netflix_titles:s1          612
  netflix_titles:s10         588
  ...
```

Then `7` with a blank prefix → a **store summary** (system store +
one line per dataset: prefix, key column, row count) followed by
the first 20 keys merged across all stores.

**Option 8 — SQL console (read-only)**

Input: `8`, target `netflix_titles`, then SQL
`SELECT COUNT(*) FROM cache_data`.

Expected:

```
  COUNT(*)
  ----------
  8807
```

Try a JSON query too (against the system store, blank target):

```sql
SELECT key, json_extract(value,'$.status') AS status
FROM cache_data WHERE key LIKE 'q:%' LIMIT 5;
```

Expected: cached search result sets (`q:...` keys), or 0 rows if
you have not searched yet.

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
           6  test:tui            <- DELETE counted too
           3  netflix_titles:s1   <- from your GETs
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

Expected: empty (all ports free). The system store's row count is
back to its pre-run value (put+delete of `test:tui` cancels out).

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
# 1st GET - miss -> the dataset's SQLite file -> populate
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1
# => ..."source":"database","node":null

# 2nd GET - now cached
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1
# => ..."source":"cache","node":"http://127.0.0.1:800x"

# Direct DB endpoints (testing-only, bypass cache)
curl.exe http://127.0.0.1:8000/db/netflix_titles:s1
# => {"key":"netflix_titles:s1","value":{...},"source":"database"}
curl.exe http://127.0.0.1:8000/db/netflix_titles:s2
curl.exe -X DELETE http://127.0.0.1:8000/db/netflix_titles:s2   # deletes DB row ONLY
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s2          # => 404 (gone from DB)
```

> The `/db/*` DELETE intentionally leaves the cache stale — that is
> how you can manufacture a `STALE` verdict for the TUI inspector.
> Re-import the dataset with `--reimport` (Phase 8) afterwards to
> restore the row.

### 5.5 Distribution demo (optional)

```powershell
# PUT a handful of keys, then read each back and print its node
1..6 | ForEach-Object {
  $key = "test:dist-$_"
  Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:8000/cache/$key" `
    -ContentType "application/json" -Body '{"value":"v","ttl":60}' | Out-Null
}
1..6 | ForEach-Object {
  $key = "test:dist-$_"
  $r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/cache/$key"
  "{0} -> {1}" -f $key, $r.node
}
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
empty — caches repopulate lazily from the DB on the next miss
(also a required observation: GET a dataset key you have not
fetched yet, e.g. `netflix_titles:s5000`, right after rejoin —
`source: database` once, then `source: cache`).

---

## 7. Phase 6 — Circuit breaker & database failure

Continues from the Phase 4/5 cluster. Breaker starts `closed`.

### 7.1 Baseline

```powershell
curl.exe http://127.0.0.1:8000/circuit-breaker/status
# => {"state":"closed","failure_count":0,"failure_threshold":3,
#     "recovery_timeout":30,...}

# Make sure a dataset key is cached (GET twice) so HITs work later
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1
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
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1
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

Wait >30s (recovery cooldown), then a MISS triggers the HALF-OPEN
probe (a cache HIT never touches the DB and cannot heal the breaker,
so use a key nothing has fetched yet):

```powershell
curl.exe http://127.0.0.1:8000/cache/definitely:missing5
# => HTTP 404 (the DB was probed and answered normally)

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
  curl.exe -X PUT http://127.0.0.1:8000/cache/test:hot `
    -H "Content-Type: application/json" -d '{"value":"hot","ttl":300}' | Out-Null
  curl.exe http://127.0.0.1:8000/cache/test:hot | Out-Null
  curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1 | Out-Null
}
```

### 8.2 Cluster hot-key ranking

```powershell
curl.exe "http://127.0.0.1:8000/cluster/hot-keys?limit=5&per_node=100"
```

Expected: `top_keys` led by `test:hot` (highest count), a
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

Pass criteria: hot list ranks `test:hot` first → restart → warm-up
`running`→`done` with the hot key loaded → node READY only after
warm-up completes → manual trigger works, unknown node 404.

> Cold-boot note: if the router itself just started (no traffic
> yet), warm-up completes instantly with `keys_attempted: 0` and
> the node goes READY immediately. Warm-up is an optimization and
> must never block recovery — seeing instant-done on a cold
> cluster is CORRECT, not a failure.

---

## 9. Phase 8 — Dataset pipeline (import → serve)

Verifies the file → per-dataset SQLite → registry → serve chain end
to end. The TUI already seeded `datasets/` on boot (Phase 3); this
phase exercises the CLI explicitly.

### 9.1 Dry-run first (parses + plans, writes nothing)

```powershell
python import_dataset.py --file datasets/netflix_titles.csv --dry-run
```

Expected: prints the detected format, the sniffed columns, the
detected key column (`show_id`), the prefix (`netflix_titles:`) and
finally `Dry run       : nothing was written`. Row counts in
`--list` are unchanged.

### 9.2 Seed the whole drop folder (idempotent)

```powershell
python import_dataset.py --seed
```

Expected on a fresh folder: one report block per file (File /
Columns / Key column / Rows written / Database ...). Run it again:
`drop folder is empty or fully imported` — a file is only
re-imported when its content changed.

### 9.3 Verify

```powershell
python import_dataset.py --list
```

Expected — one line per dataset (live rows, prefix, key column):

```
imdb_movies                      imdb_movies:         rows=10178    key=(row #)          imdb_movies.csv
netflix_titles                   netflix_titles:      rows=8807     key=show_id          netflix_titles.csv
zomato_restaurant_dataset        ...                  rows=9551     key=Restaurant ID    Zomato Restaurant Dataset.csv
```

Each dataset is its own physical SQLite file:

```powershell
Get-ChildItem data\datasets
# imdb_movies.db  netflix_titles.db  zomato_restaurant_dataset.db  ...
```

### 9.4 Serve it through the cluster

With the cluster running (restart via `python tui.py` or the
4-terminal setup if you stopped it):

```powershell
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1   # 1st: source "database"
curl.exe http://127.0.0.1:8000/cache/netflix_titles:s1   # 2nd: source "cache"
curl.exe http://127.0.0.1:8000/db/imdb_movies:1          # direct read from that dataset's file
```

Or simply use TUI options 3/5/6/7/8 from Phase 3 — same data.

Import tests are covered by `pytest tests/test_import_dataset.py
tests/test_registry.py tests/test_multi_repository.py -v` (63 tests).

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

## 11. Phase 10 — Web UI & search caching (Layer 8)

Verifies the vanilla-JS web UI served by the router and the
cached-search path that is Layer 8's headline claim. With the
4-terminal cluster running (Phase 4) and the dataset loaded
(Phase 8):

### 11.1 The UI is served by the router

```powershell
curl.exe -I http://127.0.0.1:8000/
curl.exe -s http://127.0.0.1:8000/ui | Select-String "<title>"
```

Expected: `/` answers **307** and redirects to `/ui`; `/ui` answers
**200** with the SPA HTML. `/ui/style.css` and `/ui/app.js` also
answer 200. There is no build step — the files under `frontend/`
are served as-is.

### 11.2 Search runs SQL once, then serves from cache

```powershell
curl.exe "http://127.0.0.1:8000/search/netflix_titles?q=breaking&sort=release_year&dir=asc"
curl.exe "http://127.0.0.1:8000/search/netflix_titles?q=breaking&sort=release_year&dir=asc"
```

Expected: the **first** response carries `"source":"database"` and
a non-zero `took_ms`; the **repeat** carries `"source":"cache"`,
a `"node"` URL and `took_ms` near zero. Both share the same
`cache_key` (starting `q:netflix_titles:`). The UI shows this as a
`🗄 DATABASE` → `⚡ CACHE` banner flip on the same query.

### 11.3 Facets, sorting and per-dataset entities

```powershell
# One entity per imported dataset; facet/range params come from
# /search/entities (generated from each dataset's schema)
curl.exe "http://127.0.0.1:8000/search/netflix_titles?type=Movie&release_year_min=2018&release_year_max=2021"
curl.exe "http://127.0.0.1:8000/search/netflix_titles?rating=TV-MA&sort=release_year&dir=desc"
```

Expected: each returns 200 with a `total` that only counts matching
rows; every item in `items` satisfies every filter. An unknown
entity (`/search/aliens`) returns **422**, and an injected sort
(`sort=price;DROP`) is silently rejected by the whitelist (falls
back to the default sort, nothing breaks).

### 11.4 Telemetry proves the load reduction

```powershell
curl.exe -X POST http://127.0.0.1:8000/dashboard/telemetry/reset
# ... run the same search ~10 times ...
curl.exe -s http://127.0.0.1:8000/dashboard/aggregate
```

Expected inside `telemetry`: `search_database` stays at **1**,
`search_cache` climbs to **9**, and `db_load_reduction_pct` reads
**90.0**. The counters count real operations — a single-flight
coalesced read is exactly one database hit, not one per request.

### 11.5 The UI end-to-end

Open `http://127.0.0.1:8000/` and walk the four tabs:

1. **Search** — run a query twice; watch the source banner flip
   amber → green. Toggle a facet; the chips update.
2. **Dashboard** — every node's hit rate is 0–100, the telemetry
   panel matches the curl numbers, hot keys are listed.
3. **Inspector** — enter `netflix_titles:s1`; cache and DB values agree.
   Browse the prefix `q:` to see the cached result sets themselves.
4. **Cache Lab** — pick a query, iterations = 20, press **Run**.
   The cumulative chart shows 1 SQL run then a flat line, with
   `95.0%` (or thereabouts) reduction displayed.

Pass criteria: UI served at `/ui`, first search `database` then
`cache`, facets filter correctly, telemetry shows `search_cache`
climbing while `search_database` stays at 1, and the Cache Lab
chart visibly plateaus.

---

## 12. Master checklist

Copy this table into the session notes and tick every row.

| # | Feature | Phase | Pass criteria |
|---|---|---|---|
| 1 | Test suite green | 0 | 303 passed |
| 2 | Tests are hermetic | 0 | registry + dataset files unchanged after pytest |
| 3 | Import dry-run (no servers) | 1 | plan printed, `nothing was written` |
| 4 | Registry listing | 1 | one line per dataset, no traceback |
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
| 17 | Prefix listing | 3 | `8807 key(s) matching 'netflix_titles:'` |
| 18 | SQL COUNT(*) | 3 | `8807` against the `netflix_titles` store |
| 19 | SQL json_extract query | 3 | rows for `q:` cache keys (or 0) |
| 20 | SQL rejects writes | 3 | red error, nothing deleted |
| 21 | Stats/hot-keys/breaker view | 3 | breaker closed, keys listed |
| 22 | TUI clean stop | 3 | option 0 frees all 4 ports |
| 23 | Router /cluster/status gate | 4 | 503 until ready, then 3/3 |
| 24 | Replicated PUT via curl | 4 | `replicated_to: 2` |
| 25 | GET via curl | 4 | `source: cache` |
| 26 | DELETE via curl | 4 | `deleted_from_database: true` |
| 27 | /db/* direct endpoints | 4 | read + delete bypass cache |
| 28 | Distribution loop (§5.5) | 4 | keys spread over nodes |
| 29 | Node failure detection | 5 | `[NODE FAILED]` in ~6–20s |
| 30 | Replica survives kill | 5 | GET returns value while 2/3 |
| 31 | Gated rejoin | 5 | RECOVERING then READY |
| 32 | Empty node refills lazily | 5 | 1st GET after rejoin `database` |
| 33 | Breaker opens at 3 failures | 6 | state `open`, 503s |
| 34 | Breaker fast-fails when open | 6 | <0.1s response |
| 35 | HITs served while open | 6 | 200 `source: cache` |
| 36 | Breaker heals after restore | 6 | closed after cooldown+probe |
| 37 | Writes protected by breaker | 6 | PUT 503 while DB down |
| 38 | Cluster hot-key ranking | 7 | test:hot on top |
| 39 | Warm-up on restart | 7 | `running` → `done` → READY |
| 40 | Manual warm-up trigger | 7 | works; unknown node 404 |
| 41 | Seed drop folder | 8 | report per file; 2nd run is a no-op |
| 42 | Import dry-run | 8 | validates, writes nothing |
| 43 | Import full | 8 | `rows=8807 key=show_id`; own `data/datasets/*.db` |
| 44 | Imported data served | 8 | cache-aside works on netflix_titles:s1 |
| 45 | /cluster/replication status | 9 | sequence, nodes, empty failovers |
| 46 | Write bumps sequence + acks | 9 | acked == current, behind 0 |
| 47 | Node entry carries sequence | 9 | `/internal/entry` shows sequence |
| 48 | Manual resync + 404 | 9 | done; unknown node 404 |
| 49 | Failover recorded on kill | 9 | last_failover + history, 2/3 |
| 50 | Replication unit tests | 0 | 34 replication tests pass |
| 51 | UI served at /ui | 10 | `/` 307 redirect, `/ui` 200 HTML |
| 52 | Search SQL then cache | 10 | 1st `database`, repeat `cache` |
| 53 | Facets + entity filters | 10 | total only counts matching rows |
| 54 | Unknown entity / bad sort | 10 | 422 / silent fallback, no crash |
| 55 | Telemetry load reduction | 10 | `search_database` stays 1 |
| 56 | Cache Lab demo | 10 | chart plateaus, ~95% reduction |

Result: ___/56 passed  |  Date: ______  |  Tester: ______

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pytest`: 304+ or <303 tests | wrong interpreter / stale checkout | activate `.venv`, `git pull`, re-run |
| Port already in use | leftover process from a crashed session | `Get-NetTCPConnection -LocalPort 8000 -State Listen` → note `OwningProcess` → `Stop-Process -Id <pid> -F` |
| TUI stuck at `waiting for 3 node(s)` | a node window failed to start | check the three node windows for tracebacks; `Ctrl+C` the TUI and restart |
| PUT returns 503 right after boot | gated startup — nodes still `starting/recovering` | wait for `/cluster/status` to list them as `ready` (~5s) — expected behavior |
| Every request is slow (~2s) | something talks to `localhost` (IPv6 fallback penalty) | use `127.0.0.1` in scripts/demos |
| `curl` rejects `-X PUT` | PowerShell alias to Invoke-WebRequest | use `curl.exe` |
| TUI opens no windows | console-session limitation (e.g. some remote shells) | run the 4-terminal manual setup (Phase 4) instead |
| `Move-Item data\cache.db` → in use | SQLite connection still open from the last request | wait 1–2s, retry; the repo uses short-lived connections |
| Breaker never returns to `closed` | cooldown not elapsed, or only HITs issued since restore | wait the full 30s, then GET a key you have never fetched (only a miss probes the DB) |
| Empty `data\cache.db` after breaker test | SQLite recreated a shell file while the DB was "away" | the restore steps in §7.5 (`Remove-Item` shell first) handle it |
| `activate` blocked | execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Tests fail with DB errors | someone set `CACHE_DB_PATH` globally | unset it: `Remove-Item Env:CACHE_DB_PATH` |

---

## 14. Cleanup

End of session — all of it takes <30s:

```powershell
# 1. Stop the cluster
#    - TUI: option 0 (stops what it spawned)
#    - Manual: Ctrl+C in each of the 4 terminals

# 2. Ports free?
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003 `
  -State Listen -ErrorAction SilentlyContinue
# => empty

# 3. Datasets intact? (row counts unchanged;
#    repair a damaged one with: python import_dataset.py --file <f> --reimport)
python import_dataset.py --list

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

1. `pytest -q` → 303 passed (Phase 0)
2. `python tui.py` → seeds datasets/, then `active: 3/3` (Phase 3.1)
3. TUI `3` → `netflix_titles:s1` twice → `database` then `cache`
4. TUI `6` → `netflix_titles:s1` → `CONSISTENT`
5. TUI `8` → target `netflix_titles` → `SELECT COUNT(*) FROM cache_data` → `8807`
6. TUI `0` → ports free
7. `start http://127.0.0.1:8000/` → UI loads; search twice → banner flips
   (Phase 10)

If those seven pass, the core (cluster + cache-aside + DB + TUI +
web UI) is healthy; run the full guide before a release or demo.
