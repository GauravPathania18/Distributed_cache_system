# DCS — Distributed Cache System

An educational, from-scratch distributed cache written in Python.
It grows one layer at a time: a local LRU cache becomes a cluster of
nodes behind a router, with consistent hashing, replication, failure
detection, a database source of truth, and finally intelligent
recovery when the cluster restarts.

- **Language / runtime:** Python 3.10+
- **HTTP layer:** FastAPI + Uvicorn
- **Source of truth:** SQLite (no external services required)
- **Web UI:** http://127.0.0.1:8000/ (vanilla HTML/JS, no build step)
- **Tests:** pytest — 386 tests

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
| **6.5** | **Cold-start seeding — what an empty cache starts with** | **Done** |
| **7.1** | **Replica selection (placement)** | **Done** |
| **7.2** | **Replicated writes (primary → replicas, internal endpoint)** | **Done** |
| **7.3** | **Replication log, sequence versions & sync** | **Done** |
| **7.4** | **Failure detection formalized** | **Done** |
| **7.5** | **Automatic failover (recorded + history)** | **Done** |
| **7.6** | **Re-replication & rebalancing** | **Done** |
| **8.1** | **Cached search (query result sets as cache entries)** | **Done** |
| **8.2** | **Router-side telemetry (DB load reduction metric)** | **Done** |
| **8.3** | **Web UI — search, dashboard, inspector, cache lab** | **Done** |
| **9.1** | **Cluster control — spawn / scale / stop real node processes from the UI** | **Done** |
| **9.2** | **Dataset admin — entities as isolated databases, managed over the API** | **Done** |
| **9.3** | **Deletion split (evict vs. database delete) + 6-tab UI rework** | **Done** |

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

### Layer 6.5 in one paragraph

Warm-up answers "what did the cluster *learn* is hot?" — but a
truly fresh boot has learned nothing yet, so nodes would come up
empty (every first request a database miss) or, worse, filled with
everything. Seeding decides the first contents from the **data**
instead: each dataset is ranked by the column its schema already
flags as its popularity signal — the same default sort the UI
shows (`score`, `aggregate_rating`, `release_year`, …) — and its
top rows become the candidate pool, interleaved across datasets so
every node's slice stays diverse (`SEED_STRATEGY=random` samples
uniformly instead; datasets with no numeric/date column are
sampled too). The seeder only speaks while the hot-key ranking is
*empty* — traffic history always wins — and each node loads at
most `capacity × SEED_FILL_RATIO` of the keys it will own once the
cluster is back at full membership (during a full boot every node
is gated, so ownership is computed on the *declared* membership —
each node gets a distinct slice instead of all three seeding the
identical top keys), so seeding can never overflow a node and
evict its own seeds (the capacity is read from the node's own
`/stats`). Seeds are a starting set,
not a reservation: normal cache-aside takes over from there — a
miss reads the database and the LRU evicts the
least-recently-used row, while every access moves a key back to
the MRU front, which is how a frequently read entry keeps its
priority. A fresh process also skips the replication snapshot
(its log is empty, so there is nothing to restore): persisted
`q:`/`s:` search-cache rows regenerate through normal
write-through, leaving the initial cache exactly the seeded subset.

### Layer 8 in one paragraph

Everything so far has been plumbing; Layer 8 is what you see. A
vanilla HTML/CSS/JS single-page app is served by the router itself
at `/` (redirect) and `/ui` — no build step, no npm, no CORS. The
**Search** view turns the cache into a browsing experience: keyword
search, facets, ranges and sorting across **every dataset you
import** — filters and sorts are generated from each dataset's
sniffed schema, so dropping `netflix_titles.csv` into `datasets/`
immediately gives you type/rating chips and release-year ranges.
Every result set is written through the existing
cache layer as a `q:` entry with a 300-second TTL, so the second
identical query is answered from a node in ~0 ms. A banner on every
result tells you whether it came from `⚡ CACHE` or `🗄 DATABASE`.
The **Dashboard** view aggregates every node's hit rate (normalized
to %), the router's own telemetry (`db_reads` vs `db_reads_avoided`),
the circuit breaker, hot keys and the replication log. The
**Inspector** reads any key straight from the cache layer and can
browse a prefix — including the `q:` result sets themselves. The
**Cache Lab** runs a fixed query N times and charts the cumulative
cache hits against SQL queries, making the DB-load-reduction number
visibly obvious (e.g. 20 runs → 1 SQL + 19 cache hits = 95%
reduction). Telemetry is counted on real operations in
`services/telemetry.py`, not on HTTP requests, so a single-flight
coalesced read counts exactly one database hit.

### Layer 9 in one paragraph

Layer 9 makes the cluster *operable* from that UI. The **Cluster**
tab talks to `/cluster/nodes`: scaling up spawns real `main.py`
processes from a port pool (`NODE_PORT_BASE..+MAX_NODES-1`), scaling
down gracefully stops them and deregisters them, and every node card
offers start / stop / restart with live state, PID and cache stats —
"3 nodes" means three *running* processes, not three tombstones (the
router's bootstrapped members are started too, and anything that
already answers is adopted, never spawned twice). The **Datasets**
tab creates a new entity as its own isolated SQLite file
(`data/datasets/<name>.db`) with a sniffed schema, adds entries as
JSON rows (key derived from the declared `key_column` → `id` → a
sequence number), and deletes rows or whole datasets — every mutation
purges the affected `q:`/`s:` search caches on disk *and* in the
nodes, drops matching replication-log entries and re-syncs the
stats, so a stale search page can never outlive its data. Finally
the deletion path is split in two: `DELETE /cache/{key}` only **evicts**
the node copies (the database row survives — 404 when no node held
it), while `DELETE /db/{key}` **deletes** the row everywhere
(database + node copies + search caches). The Inspector shows both
buttons side by side, and the **Lab** adds a node-failure drill and
an eviction burst next to the search/point-read storms.

---

## Quick start

```powershell
# 1. Environment (one time)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. THE one command: imports everything sitting in datasets/
#    (once), opens the 3 nodes + router, each in its own
#    visible console window, waits for the cluster, then shows an
#    interactive control/query UI (option 0 stops everything)
python tui.py
```

Manual startup (4 terminals), if you prefer:

```powershell
# Terminal 1-3: cache nodes
python main.py --port 8001 --capacity 100
python main.py --port 8002 --capacity 100
python main.py --port 8003 --capacity 100

# Terminal 4: router (do NOT use --reload)
uvicorn router.router:app --port 8000

# Watch the cluster come up
curl http://localhost:8000/cluster/status

# Then open the web UI
start http://127.0.0.1:8000/
```

> Right after the router starts, nodes are gated
> (`starting` → `recovering` → `ready`) and the ring is empty.
> PUTs return 503 until the nodes are `ready`; wait for
> `/cluster/status` to list them under `active_nodes`.

**Easiest path (no extra terminals):** start only the router —
`uvicorn router.router:app --port 8000` — open
http://127.0.0.1:8000/, go to the **Cluster** tab and press
**+ node** (or *Scale*). The router spawns `main.py` processes itself
and gates them through warm-up like any other boot. **− node** stops
them again; the per-card buttons start/stop/restart single nodes.

Configuration (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `CACHE_NODES` | `http://127.0.0.1:8001,...:8003` | Comma-separated node URLs (or bare ports) |
| `CACHE_DB_PATH` | `data/cache.db` | System SQLite location (registry `data/registry.json` + dataset files `data/datasets/*.db` derive from it) |
| `RECOVERY_THRESHOLD` | `2` | Healthy checks required in `RECOVERING` before `READY` |
| `CLUSTER_BOOTSTRAP_STATE` | `starting` | Initial state of declared nodes (`starting` / `ready`) |
| `WARMUP_ENABLED` | `on` | Warm-up master switch (off = ungated READY) |
| `WARMUP_LIMIT` | `1000` | Top-K keys reloaded during warm-up |
| `WARMUP_RATE_LIMIT` | `100` | Max database reads/second during warm-up |
| `HOTKEY_MAX_KEYS` | `10000` | Bound on the router-side hot-key ranking |
| `SEED_ENABLED` | `on` | Cold-start seeding switch (only consulted while the hot-key ranking is empty) |
| `SEED_STRATEGY` | `popular` | Seed selection: `popular` (top rows per dataset's popularity column) or `random` (uniform sample) |
| `SEED_FILL_RATIO` | `0.6` | Fraction of a node's own capacity seeding may claim — the rest stays free for live traffic |
| `SEED_PER_DATASET` | `250` | Candidate rows pulled per dataset (the pool is then filtered to what each node owns) |
| `SEED_FALLBACK_CAPACITY` | `100` | Capacity assumed for a node that does not answer `/stats` |
| `MAX_NODES` | `8` | Size of the node port pool the UI may spawn into |
| `NODE_PORT_BASE` | `8001` | First port of that pool (`8001…8008` by default) |
| `NODE_CAPACITY` | `100` | Cache capacity given to nodes spawned by the router |
| `NODE_START_TIMEOUT` | `10` | Seconds a scale/start request waits for a fresh node to answer `/health` |

---

## HTTP API

All endpoints are served by the router (`:8000`). Cache & database:

| Method | Path | Purpose |
|---|---|---|
| GET | `/cache/{key}` | Read: node copies first, then the database |
| PUT | `/cache/{key}` | Write-through to owning nodes + DB row; `nodes: []` → 200 with a `warning` (nothing to replicate to) |
| DELETE | `/cache/{key}` | **Evict only** — drops the node copies, keeps the DB row (`404` if no node held it) |
| GET | `/db/{key}` | Read the database row directly |
| DELETE | `/db/{key}` | **Delete everywhere** — DB row + node copies + matching `q:`/`s:` search caches |

Cluster control (Layer 9 — these spawn/stop real processes):

| Method | Path | Purpose |
|---|---|---|
| GET | `/cluster/nodes` | Membership + pool: per-node `state`, `process` (`owned`/`attached`/`stopped`), `pid`, `stats`, plus `total`/`active`/`max_nodes` |
| POST | `/cluster/nodes` | `{"target": 0..MAX_NODES}` — scale: grow spawns from the pool, shrink stops and deregisters the highest ports; equal target starts any dead members |
| POST | `/cluster/nodes/{port}/start` | Spawn & register a single pool port (400 outside the pool) |
| POST | `/cluster/nodes/{port}/stop` | Graceful `/internal/shutdown`, then deregister |
| POST | `/cluster/nodes/{port}/restart` | Stop + start the same port |

Dataset administration (each entity = one isolated SQLite file):

| Method | Path | Purpose |
|---|---|---|
| GET | `/datasets` | List entities (name, rows, schema, db path) |
| POST | `/datasets` | `{"name": ..., "key_column": ...}` — create the entity (slugified name, file materialized) |
| GET | `/datasets/{name}` | One entity's metadata |
| DELETE | `/datasets/{name}` | Delete the entity everywhere: node copies by prefix, search caches, replication log, registry row |
| GET | `/datasets/{name}/entries` | Browse rows (`?limit`/`?offset`) |
| POST | `/datasets/{name}/entries` | `{"value": {...}}` — upsert one row; key derived from `key_column` → `id` → sequence; purges search caches + re-syncs stats |
| DELETE | `/datasets/{name}/entries?key=...` | Delete one row (DB row + node copies + search-cache purge) |

Search, dashboard and cluster introspection (`/search/*`,
`/dashboard/*`, `/cluster/status`, `/cluster/hot-keys`,
`/cluster/warmup`, `/cluster/replication`, `/circuit-breaker/status`,
`/health`, …) are unchanged from Layer 8 and listed with curl
examples in [`commands.md`](commands.md).

---

## Web UI — the 6 tabs

| Tab | What it is for |
|---|---|
| **Search** | Keyword/facet/range search over every dataset, with `⚡ CACHE` vs `🗄 DATABASE` banners and an entity picker for the Lab storms |
| **Dashboard** | Node hit rates, router DB-load-reduction telemetry, circuit breaker, hot keys, replication log |
| **Cluster** | Scale ±/set the cluster, per-node cards with state, process (owned/attached/stopped), PID, stats and start/stop/restart buttons; auto-refreshes every 3 s |
| **Datasets** | Create an entity (new isolated DB), browse its entries, add a JSON row, delete single rows or the whole dataset |
| **Inspector** | Inspect any key and browse a prefix — with the split **Evict from cache** / **Delete from database** buttons and a verdict line |
| **Lab** | Search storm (entity selectable), point-read storm, node-failure drill (kill a node mid-run) and eviction burst — each with a live verdict |

---

## Tests

```powershell
pytest -v
```

Expected: **386 passed** — 39 search + 37 replication +
29 dataset import + 28 node lifecycle + 28 warm-up + 21 registry +
20 repository + 18 multi-dataset repository + 18 seeding + 16 cache +
16 consistent hash + 15 statistics + 13 cache-service +
13 terminal UI + 12 process manager + 12 dataset routes +
11 circuit-breaker + 9 cluster-control routes + 9 router +
7 telemetry + 6 config + 5 single-flight + 4 dashboard.

### Bringing your own data

DCS ships with **no synthetic data**. Drop any real dataset file —
CSV, TSV, JSON, JSONL, Parquet, Excel or a Zip containing one —
into `datasets/`:

```powershell
# Drop a file in, then either restart the TUI (it auto-seeds on boot)...
python tui.py

# ...or import explicitly:
python import_dataset.py --file datasets/netflix_titles.csv
python import_dataset.py --seed          # import everything in datasets/
python import_dataset.py --list          # what's registered
```

Each file becomes its **own SQLite database** (`data/datasets/<name>.db`)
registered in `data/registry.json`. The key column is auto-detected
(`id`, `show_id`, `Restaurant ID`, ... — or row numbers as a fallback),
the schema is sniffed, and the search UI builds its filters and sorts
from it. Nothing is hardcoded: one imported file = one searchable entity.
Imported data persists until you delete the `data/` files.

Full command reference, curl examples and live fault-tolerance
walkthroughs live in [`commands.md`](commands.md).
For the internal file-by-file implementation and import reference, see
[`PROJECT_GUIDE.md`](PROJECT_GUIDE.md).

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
