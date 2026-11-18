# Distributed Cache System — Engineering Reference

> Internal project documentation for engineers. This describes the code
> currently present in the repository; it is not a product or end-user guide.

## 1. Repository scope

This guide covers the project's Python source, frontend, and
project-level metadata/documentation files. It describes
the purpose of each file, the problems it addresses, its principal classes or
functions, and why its imports are needed.

Generated data, local environments, research materials, and report
artifacts are excluded from this inventory. In particular,
`.gitignore` excludes `data/`, `datasets/`, `.venv/`, `/DCS_research`,
the capstone report files (`DCS_Capstone_Report_*`, `PersonalLLM_*`)
and the whole `_report_bench/` tree — while the pytest suite under
`tests/` is tracked. The README and `commands.md` state the expected
test counts.

## 2. Problem statement and solution

### Problem

Applications that repeatedly load the same values from a persistent store can
incur unnecessary latency and storage load. A single in-memory cache is fast
but volatile, limited in capacity, and a single point of failure. A cluster
adds routing, replica selection, failure detection, and recovery challenges.
Restarting a cache node also creates a cold cache and can send a burst of
requests to the source of truth.

### What this project implements

DCS is an educational Python implementation of a distributed cache with:

- A thread-safe, in-memory LRU cache with TTL expiration and eviction.
- Separate HTTP cache-node processes, served with FastAPI/Uvicorn.
- Consistent-hash placement with virtual nodes and distinct-node replica
  selection.
- A router that fans cache operations out to the selected nodes.
- SQLite as the durable source of truth, with cache-aside reads and
  database-first writes/deletes.
- Per-key single-flight loading and a database circuit breaker.
- Node lifecycle states, periodic health checks, and gated recovery.
- In-memory operation statistics, bounded hot-key tracking, and throttled
  cache warm-up before a recovering node re-enters the ring.
- Cold-start seeding: a brand-new boot has no traffic history to
  replay, so each node's first contents are chosen from the **data**
  (every dataset's top rows by its popularity column), interleaved
  across datasets and capped at a fraction of the node's capacity.
- Replicated writes with a sequence-numbered log, sync/resync, recorded
  failover and re-replication back onto healthy nodes.
- Cached keyword/facet search over the datasets, router-side DB-load
  telemetry, and a six-tab web UI (Search, Dashboard, Cluster,
  Datasets, Inspector, Lab) served by the router itself.
- Cluster control and dataset administration over HTTP: spawn, scale,
  stop and restart real node processes; create entities as isolated
  SQLite files; browse, add and delete their rows — with every
  mutation purging the search caches it invalidates. Deletion itself
  is split: evict the cache copies, or delete the database row
  everywhere.
- A one-command launcher (`run.py`) that seeds the drop folder, spawns
  the node processes and runs the router, plus a file-based dataset
  importer.

### Request and recovery paths

**GET:** client → router `CacheService` → distributed cache (primary followed
by replicas) → on miss, single-flight → circuit breaker → SQLite → populate
cache. A cache hit does not require a database call.

**PUT:** router → SQLite first → cache nodes chosen by the hash ring. The HTTP
API reports failure if no cache node accepted the value, even though the
database write may already have succeeded.

**DELETE (split, Layer 9):** `DELETE /cache/{key}` evicts the key from
the selected cache nodes and keeps the SQLite row (404 when no node
held it). `DELETE /db/{key}` deletes the SQLite row first and then
cascades the eviction plus a purge of the affected `q:`/`s:` search
caches, so no later read can resurrect stale data.

**Node recovery:** health checks drive `STARTING` / `FAILED` nodes into
`RECOVERING`; they remain out of the live ring. Warm-up merges hot-key data,
selects keys for which that node would be primary on a ring view including the
node, throttles database reads, and writes keys directly to that node. The
node becomes `READY` only after both the configured healthy-check threshold
and the recovery gate are satisfied.

### Data ownership and consistency assumptions

- SQLite is the durable source of truth. Cache-node memory is disposable.
- Cache values are JSON-serialized in SQLite; cache TTL applies only to the
  in-memory cache, not to the database record.
- Cache nodes do not access SQLite directly; the router owns orchestration.
- Replication is a fan-out of an operation to the distinct nodes
  returned by `NodeManager`, sequenced by a cluster-wide counter the
  router owns: each mutation is recorded in a bounded **in-memory**
  log before it fans out, nodes acknowledge the highest sequence they
  applied, `POST /cluster/resync/{node:path}` catches a lagging node
  up (incremental replay or full snapshot) and re-replication restores
  the replication factor after a failure. There is still no replica
  quorum, version vector, durable journal, or continuous anti-entropy
  protocol — nodes never talk to each other, only the router does.
- `SingleFlight` coalesces requests only inside one Python process.
- Node statistics and router hot-key rankings are in-memory process state;
  they are not durable across router restarts.

## 3. Source tree and file-by-file responsibilities

### 3.1 `cache/` — cache engine and usage statistics

#### [`cache/__init__.py`](./cache/__init__.py)

- **Purpose/problem solved:** Defines the package's intended public imports so
  callers can use `from cache import LRUCache` instead of knowing module
  internals.
- **Public symbols:** `HotKeyManager`, `LRUCache`, `Node`,
  `CacheStatistics`, exported through `__all__`.
- **Imports:** The four relative imports load those public implementations.

#### [`cache/node.py`](./cache/node.py)

- **Purpose/problem solved:** Represents one cache entry and its links in the
  LRU doubly linked list; centralizes TTL-expiration checking.
- **Functions:** `Node.__init__` stores key, value, expiration, and list
  pointers; `is_expired(current_time)` checks expiration; `__repr__` supports
  readable debugging output.
- **Imports:** `Any` allows arbitrary cache values; `Optional` types the
  optional expiry and links.

#### [`cache/_linked_list_ops.py`](./cache/_linked_list_ops.py)

- **Purpose/problem solved:** Keeps LRU list pointer manipulation separate
  from cache lookup/locking policy.
- **Functions:** `_remove_node`, `_add_to_front`, `_move_to_front`, and
  `_remove_lru` implement O(1) recency changes and eviction from the tail.
- **Imports:** `Node` is the list element type; `Optional` describes an empty
  list result.

#### [`cache/_load_state.py`](./cache/_load_state.py)

- **Purpose/problem solved:** Provides the per-key synchronization record
  used by `LRUCache.get_or_set` to prevent concurrent callers from running
  the same loader.
- **Functions/classes:** `_LoadState` owns a completion `Event` and optional
  loader exception.
- **Imports:** `threading.Event` blocks followers until the loader finishes;
  `Optional` types the exception field.

#### [`cache/_cache_stats.py`](./cache/_cache_stats.py)

- **Purpose/problem solved:** Holds the cache's inspection methods outside
  the main cache class implementation.
- **Functions:** `size()` reports stored entries; `stats()` reports
  capacity, counts, evictions, and hit rate; `keys()` returns most-recently-
  used to least-recently-used order; `__repr__()` formats a cache summary.
- **Imports:** None. It relies on state supplied by `LRUCache` through
  inheritance.

#### [`cache/lru_cache.py`](./cache/lru_cache.py)

- **Purpose/problem solved:** Implements the actual bounded, thread-safe
  in-memory key/value store and its cache operations.
- **Class/functions:** `LRUCache.__init__` validates capacity and prepares
  the map, sentinel list nodes, lock, counters, eviction hook, and
  per-key-load map. `get()` checks TTL, records hits/misses, and moves hits to
  the front. `get_or_set()` coalesces concurrent loads for one key.
  `set()` inserts/updates, applies TTL, and evicts the least-recently-used
  entry when over capacity. `delete()`, `exists()`, and `clear()` remove or
  inspect entries.
- **Imports:** `time` supplies monotonic timestamps for TTL; `threading`
  protects concurrent operations; `Any`, `Callable`, `Dict`, and `Optional`
  express values, callbacks, map contents, and optional TTLs. `Node`,
  `_LoadState`, `_LinkedListOps`, and `_CacheStats` provide the entry model,
  in-flight load state, linked-list operations, and inspection methods.

#### [`cache/statistics.py`](./cache/statistics.py)

- **Purpose/problem solved:** Measures per-node cache operations and
  per-key frequency without writing telemetry into the database; bounded
  tracking and rolling windows prevent unbounded telemetry growth.
- **Class/functions:** `CacheStatistics` validates settings; `record_hit`,
  `record_miss`, `record_set`, `record_delete`, and `record_eviction` record
  activity; `_note_access()` enforces the distinct-key bound; `snapshot()`
  and `top_keys()` expose window information; `rotate()` snapshots and resets
  a window; `start()`, `stop()`, and `_rotation_loop()` control background
  window rotation.
- **Imports:** `threading` provides locks, events, and the rotation worker;
  `time` timestamps and schedules windows; `Counter` counts accesses and
  ranks keys.

#### [`cache/hot_keys.py`](./cache/hot_keys.py)

- **Purpose/problem solved:** Maintains the router's bounded cluster-level
  hot-key ranking, which remains available when cache nodes restart.
- **Class/functions:** `HotKeyManager.record()` counts a router-observed
  access; `merge()` combines node-local rankings and trims to the bound;
  `top_keys()`, `stats()`, and `clear()` inspect/reset the ranking.
- **Imports:** `threading` protects concurrent updates; `Counter` stores and
  ranks frequencies.

### 3.2 `cluster/` — placement, membership, health, and lifecycle

#### [`cluster/__init__.py`](./cluster/__init__.py)

- **Purpose/problem solved:** Provides a compact package-level public API.
- **Public symbols:** `ConsistentHashRing`, `NodeManager`, and `NodeState`.
- **Imports:** Relative imports expose those implementations and `__all__`
  defines the intended exports.

#### [`cluster/consistent_hash.py`](./cluster/consistent_hash.py)

- **Purpose/problem solved:** Assigns keys to cache nodes while limiting the
  amount of key movement when membership changes; also selects distinct
  physical nodes for replicas.
- **Class/functions:** `ConsistentHashRing` stores virtual-node positions.
  `_hash()` maps node/key strings to ring positions. `add_node()` and
  `remove_node()` update the ring. `get_node()` selects the primary;
  `get_nodes()` walks clockwise and returns up to the requested number of
  distinct nodes. `copy()` snapshots the ring; `with_node()` makes a snapshot
  including a gated node; `get_all_nodes()` and `__len__()` inspect it.
- **Imports:** `hashlib` computes deterministic MD5 positions; `bisect`
  maintains/searches sorted ring positions efficiently.

#### [`cluster/node_state.py`](./cluster/node_state.py)

- **Purpose/problem solved:** Defines the lifecycle state vocabulary and
  rejects invalid state transitions.
- **Classes/functions:** `NodeState` enumerates `STARTING`, `RECOVERING`,
  `READY`, `UNHEALTHY`, and `FAILED`. `ALLOWED_TRANSITIONS` is the transition
  table. `InvalidTransitionError` represents an illegal transition;
  `can_transition()` checks the table.
- **Imports:** `Enum` implements a string-valued enum suitable for both
  internal comparisons and API output.

#### [`cluster/events.py`](./cluster/events.py)

- **Purpose/problem solved:** Delivers node lifecycle changes to optional
  listeners without running external work while the manager lock is held.
- **Classes/functions:** `LifecycleEvent` stores a node and old/new states,
  and exposes readable string values. `fire_listeners()` snapshots listeners
  and calls each one, isolating listener exceptions so a callback does not
  break lifecycle progression.
- **Imports:** None; it operates on the event and manager objects passed in.

#### [`cluster/node_lifecycle.py`](./cluster/node_lifecycle.py)

- **Purpose/problem solved:** Implements state entry, ring membership side
  effects, registration, and health-result transitions in one state-machine
  driver.
- **Functions:** `enter_state()` updates state, active membership, the hash
  ring, and relevant counters. `register()` adds known nodes in a chosen
  initial state. `_recovery_gate_satisfied()` asks the optional warm-up gate
  and fails closed on a gate exception. `_transition()` combines a validated
  transition with an event. `on_health_result()` advances or fails a node
  according to consecutive health/recovery counts and fires events after
  releasing the lock.
- **Imports:** `LifecycleEvent` and `fire_listeners` communicate transitions;
  `NodeState`, `InvalidTransitionError`, and `can_transition` define and
  enforce legal states. `ROUTABLE_STATES` defines which states belong in the
  ring.

#### [`cluster/node_activation.py`](./cluster/node_activation.py)

- **Purpose/problem solved:** Retains the earlier Layer 4 direct
  activate/deactivate interface for compatibility; newer code is expected
  to use the gated lifecycle.
- **Functions:** `activate_node()` force-transitions/registers a node as
  `READY`; `deactivate_node()` force-transitions a known node to `FAILED`.
  Both notify listeners after leaving the lock.
- **Imports:** Lifecycle event creation/dispatch and `register`/`enter_state`
  implement the operations; `NodeState` supplies the target values.

#### [`cluster/health_monitor.py`](./cluster/health_monitor.py)

- **Purpose/problem solved:** Detects cache-node availability and feeds
  outcomes to the lifecycle state machine.
- **Functions:** `check_node()` calls a node's `/health`; `run_health_pass()`
  checks every known node once, feeds results to `on_health_result()`, then
  runs health hooks; `health_check_loop()` repeats passes at the configured
  interval.
- **Imports:** `requests` performs HTTP health checks; `time` schedules
  passes. `on_health_result` is imported inside `run_health_pass()` to avoid
  a module-level circular import.

#### [`cluster/node_manager.py`](./cluster/node_manager.py)

- **Purpose/problem solved:** Acts as the router-side authority for node
  membership, routing eligibility, ring snapshots, lifecycle callbacks,
  health configuration, and cluster status.
- **Class/API:** `NodeManager.__init__()` initializes membership, ring,
  counters, listeners, optional gate, and registers initial nodes.
  `get_nodes()`/`get_primary()` resolve placement. `ring_view()` and
  `expected_primary()` create/read recovery ownership views.
  `add_listener()`, `remove_listener()`, `add_health_hook()`, and
  `set_recovery_gate()` configure lifecycle integrations.
  `start_node()`, `mark_recovering()`, `mark_ready()`, `mark_failed()`,
  `is_available()`, `register_node()`, and `get_node_state()` expose
  lifecycle operations. `_mark()` centralizes validated transitions.
  `start_health_monitor()` launches health checks;
  `get_status()` returns a snapshot.
- **Imports:** `threading` provides the manager's re-entrant lock and health
  thread; `defaultdict` initializes per-node counters. The ring, lifecycle
  event helpers, health loop, and `NodeState` implement routing/lifecycle.
  Lifecycle helpers are imported inside methods where needed to avoid
  circular initialization between the manager and the state-machine module.

### 3.3 `database/` — SQLite stores, dataset registry, and prefix routing

#### [`database/__init__.py`](./database/__init__.py)

- **Purpose/problem solved:** Exposes the storage API as the package's
  public surface.
- **Public symbols:** `DatabaseRepository` (one SQLite file),
  `DatasetRegistry` (the `data/registry.json` manifest), and
  `MultiDatasetRepository` (routes keys across the system and per-dataset
  files).
- **Imports:** Relative imports and `__all__` make the three classes
  available from the package namespace.

#### [`database/exceptions.py`](./database/exceptions.py)

- **Purpose/problem solved:** Separates an unavailable/failed storage layer
  from an ordinary key miss, allowing service/API code to return a 503 rather
  than treating an outage as missing data.
- **Classes:** `DatabaseUnavailableError`.
- **Imports:** None.

#### [`database/_connection_mixin.py`](./database/_connection_mixin.py)

- **Purpose/problem solved:** Centralizes SQLite connection setup and
  creation of the key/value table.
- **Functions:** `_get_connection()` opens a configured connection and sets
  `sqlite3.Row`; `_initialize_database()` creates `cache_data` if necessary
  and translates SQLite failures.
- **Imports:** `sqlite3` provides the storage connection and errors; `Path`
  is imported but is not used in this mixin (directory creation is done by
  `repository.py`); `DatabaseUnavailableError` is the repository-level
  failure type.

#### [`database/_crud_mixin.py`](./database/_crud_mixin.py)

- **Purpose/problem solved:** Implements JSON-encoded CRUD, bulk import,
  listing/counting, and bounded SQL inspection.
- **Functions:** `query()` allows one statement beginning with `SELECT`,
  `WITH`, `PRAGMA`, or `EXPLAIN` and limits returned rows.
  `get()` decodes a JSON value; `set()` and `set_many()` upsert one or many
  records; `delete()`, `exists()`, `count()`, `keys()`, and `clear()` provide
  storage operations. SQLite errors are translated to
  `DatabaseUnavailableError`.
- **Imports:** `json` serializes/deserializes values; `sqlite3` identifies
  storage errors; `Any` and `Optional` express stored values and optional
  limits; `DatabaseUnavailableError` communicates database failures.

#### [`database/repository.py`](./database/repository.py)

- **Purpose/problem solved:** Provides the application-facing repository
  interface and keeps SQLite implementation details behind one object.
- **Class/functions:** `DatabaseRepository.__init__()` records path/timeout,
  creates the parent directory, and initializes the table. The CRUD and
  connection methods are supplied by `_ConnectionMixin` and `_CrudMixin`.
- **Imports:** `Path` creates the parent directory; the mixins supply
  connection and query behavior.

#### [`database/registry.py`](./database/registry.py)

- **Purpose/problem solved:** Turns each imported dataset file into a
  registered, physically separate SQLite database with a sniffed schema,
  so nothing about searchable entities is hardcoded.
- **Constants/functions:** `RESERVED_NAMES`/`RESERVED_PREFIXES` keep `q:`
  and `s:` free for the search cache. `slugify()` derives a dataset name
  from a file name; `safe_key()` makes key values URL-path safe;
  `coerce()` types CSV strings; `detect_schema()` samples rows to classify
  each column (string/number/date/bool, plus distinct-value lists for
  low-cardinality strings that become enum facets); `detect_key_column()`
  picks an explicit, id-style, or 100%-unique column (row-index fallback
  otherwise).
- **Class:** `DatasetRegistry` owns `data/registry.json` (paths derived
  from `CACHE_DB_PATH` so tests stay hermetic). `register()` writes one
  manifest entry plus a source-file fingerprint (size/mtime) for change
  detection; `unregister()` removes the entry and deletes the dataset's
  database file; `entries()`/`names()`/`get()` read the manifest with an
  mtime-cached reload (`revision()` bumps across instances);
  `scan_drop_folder()` lists new/changed files in the drop folder;
  `prefix_for()`/`entry_for()` resolve a key to its dataset by longest
  matching prefix.
- **Imports:** `json` persists the manifest; `os`/`re` derive paths and
  slug patterns; `time` stamps fingerprints; `pathlib.Path` touches files.

#### [`database/multi_repository.py`](./database/multi_repository.py)

- **Purpose/problem solved:** One interface over many SQLite files:
  `data/cache.db` holds search-cache (`q:`/`s:`) and manual keys, and every
  registered dataset keeps its own `data/datasets/<name>.db`.
- **Class:** `MultiDatasetRepository` mirrors the `DatabaseRepository`
  interface (`get`/`set`/`delete`/`exists`/`count`/`keys`/`iter_keys`/
  `query`/`set_many`/`clear`) so every layer above works unchanged. Each
  key is routed by its longest matching dataset prefix; unmatched keys
  fall through to the system store. `repo_for()`, `repo_named()`,
  `repos_matching()`, and `repos()` expose the individual stores for
  the dataset browser and admin tooling; `query()` infers the target store from `LIKE`
  parameters. Registry changes are picked up live via a revision check,
  and dataset files are created lazily on first touch.
- **Imports:** `heapq`/`itertools` merge sorted key streams across stores;
  `DatasetRegistry` supplies the routing table; `DatabaseRepository`
  implements each individual store.

### 3.4 `services/` — cache policy and resilience primitives

#### [`services/__init__.py`](./services/__init__.py)

- **Purpose/problem solved:** Defines convenient package-level exports.
- **Public symbols:** `CacheService`, `CacheWarmup`, `CircuitBreaker`,
  `DistributedCache`, `SingleFlight`, and `WarmupRateLimiter`.
- **Imports:** Relative imports load each service implementation.

#### [`services/distributed_cache.py`](./services/distributed_cache.py)

- **Purpose/problem solved:** Hides HTTP and ring placement from service
  policy, implementing primary/replica reads and replicated writes and
  deletes.
- **Class/functions:** `DistributedCache.__init__()` stores the node manager,
  timeout, and reusable HTTP session. `get()` tries selected nodes and
  continues after misses or request failures. `set()` sends the value/TTL to
  selected nodes and returns successful nodes. `delete()` invalidates
  selected nodes. `write_to_node()` bypasses normal placement for warm-up.
- **Imports:** `requests` performs node-to-node HTTP calls.

#### [`services/circuit_breaker.py`](./services/circuit_breaker.py)

- **Purpose/problem solved:** Prevents repeated slow/failing database calls
  after a configured failure streak and permits a limited recovery probe.
- **Class/API:** `CircuitBreaker.call()` wraps a database operation.
  `_should_reject()`, `_record_success()`, and `_record_failure()` manage the
  `CLOSED`, `OPEN`, and `HALF_OPEN` transitions. `get_state()`,
  `get_stats()`, and `reset()` expose/reset state.
- **Imports:** `threading` protects breaker state; `time` measures recovery
  timeout; `Any` and `Callable` describe wrapped operation inputs/results;
  `DatabaseUnavailableError` represents fast-fail and normalized failures.

#### [`services/single_flight.py`](./services/single_flight.py)

- **Purpose/problem solved:** Coalesces concurrent work for the same key so
  a miss storm results in one in-process load rather than many duplicate
  database reads.
- **Classes/functions:** `Flight` stores an event, result, or exception;
  `set_result()`, `set_error()`, and `get()` publish/wait for completion.
  `SingleFlight.do()` elects one leader per key and makes followers share its
  outcome; `in_flight_keys()` supports inspection.
- **Imports:** `threading` supplies events and a lock; `Any`, `Callable`, and
  `Dict` describe values, loader functions, and the in-flight map.

#### [`services/cache_service.py`](./services/cache_service.py)

- **Purpose/problem solved:** Applies cache-aside and write-through/
  invalidation policy without coupling cache nodes to the database.
- **Class/API:** `CacheService.__init__()` wires cache, repository,
  single-flight, breaker, and hot-key ranking. `get()` checks cache then
  single-flights `_load_from_database()`. The internal load double-checks
  cache, reads SQLite through the breaker, and fills the cache.
  `set()` writes database first then cache; `delete()` removes from database
  then invalidates cache. `circuit_breaker_status()` exposes breaker state.
- **Imports:** `Any` and `Optional` describe flexible values and return
  types; `Dict` is imported but is not used in this module.
  `HotKeyManager` records access frequency; the database exception supports
  outage handling; `CircuitBreaker` and `SingleFlight` provide the
  resilience/concurrency policies.

#### [`services/warmup.py`](./services/warmup.py)

- **Purpose/problem solved:** Preloads the hottest keys owned by a recovering
  node while controlling database load, and holds the node out of the hash
  ring until warm-up completes.
- **Classes/functions:** `WarmupRateLimiter.wait()` spaces database reads.
  `CacheWarmup.install()` registers lifecycle listeners, health retries, and
  a readiness gate. `_on_lifecycle()` starts/reinitializes warm-up state;
  `_retry_recovering()` retries failed attempts. `recovery_ready()` answers
  the gate. `ensure()` starts at most one worker per node;
  `status()`/`_entry()` build status snapshots. `_run()` executes the worker;
  `warm_node()` merges rankings, filters keys by ownership, loads database
  values, and writes directly to the recovering node. `_db_get()`,
  `_merge_node_stats()`, and `_fetch_node_stats()` implement database and
  node-stat access.
- **Imports:** `threading` runs background warm-up and protects state; `time`
  implements rate spacing and timestamps; `requests` fetches node statistics;
  `DatabaseUnavailableError` distinguishes a database outage that must keep
  the node gated.
- **Important behavior:** Missing database keys are skipped; DB failures mark
  the attempt failed so later health passes can retry. A failed direct cache
  write is counted but does not itself mark the node health-failed.

#### [`services/seeding.py`](./services/seeding.py)

- **Purpose/problem solved:** Layer 6.5 — decides what an *empty* node
  holds before any traffic exists, when the Layer 6.3 ranking has
  nothing to reload. Selection policy lives here; the mechanics
  (ownership, throttling, writing) stay in `warmup.py`.
- **Functions/class:** `normalize_strategy()` accepts `popular` /
  `random`; `popularity_order()` ranks a dataset by the column its
  schema flags as the popularity signal (`score`,
  `aggregate_rating`, `release_year`, …); `dataset_keys()` +
  `interleave()` build the cross-dataset candidate pool and
  `seed_keys()` is the store-level entry point; `fetch_capacity()`
  reads a node's real capacity from `/stats` and `seed_budget()`
  computes `capacity × SEED_FILL_RATIO`. `CacheSeeder.candidates()`,
  `budget(node)` and `status()` (reported as `seed` by
  `GET /cluster/warmup`) are what `CacheWarmup` calls — a missing
  seeder means seeding is off.
- **Imports:** `requests` fetches node capacity; `build_entities()`
  from `services.search` reuses each entity's default sort as its
  popularity order; `DatabaseQueryError` /
  `DatabaseUnavailableError` separate "nothing to seed" from a DB
  outage (which must keep the node gated).

#### [`services/replication.py`](./services/replication.py)

- **Purpose/problem solved:** Layer 7.3–7.6 — router-side write
  ordering. The router is the single sequencing authority, so nodes
  never talk to each other and replication loops are structurally
  impossible.
- **Class/functions:** `ReplicationManager.next_sequence()` allocates
  the cluster-wide sequence; `record()` appends each mutation to a
  bounded in-memory `deque` *before* the fan-out; `ack()` /
  `acked_sequence()` keep per-node high-water marks; `entries_since()`
  and `log_oldest_sequence()` back the incremental path, while
  `_needs_snapshot()` / `_snapshot_node()` switch to a full DB pull
  when the gap is large; `sync_node()` runs the sync, `repair()` /
  `repair_active_nodes()` re-replicate after a failure (Layer 7.6),
  `record_failover()` / `failovers()` / `last_failover()` keep the
  history, and `status()` is what `GET /cluster/replication` returns.
  `install()` hooks the lifecycle so a `RECOVERING` node is synced
  with the same retry-per-health-pass pattern warm-up uses.
- **Imports:** `deque` is the bounded log; `threading` enforces the
  single-writer status model; `DatabaseUnavailableError` keeps a DB
  outage from being mistaken for a completed sync.

#### [`services/search.py`](./services/search.py)

- **Purpose/problem solved:** Layer 8.1 — keyword/facet/range/sort
  search over every registered dataset, where each distinct query
  combination is one cache entry (`q:` prefix, `SEARCH_TTL`) so a
  repeated search never touches SQL again.
- **Functions/class:** `build_entities()` turns each dataset's
  sniffed schema into the filters, ranges, sortable columns and
  suggest fields the UI renders; `_safe()` canonicalizes user values
  into URL-path-safe cache-key fragments (case-folded, `/` forbidden);
  `SearchService.entities()`, `search()` (build `q:` key → cache hit
  or run `_run()`/`_where()`/`_order()` SQL → write through the cache),
  `suggest()` (separate `s:` keys) and `browse()` (prefix paging used
  by the Inspector).
- **Imports:** `math`/`re`/`time` implement pagination, key
  sanitization and the `took_ms`/`db_took_ms` timings; the database
  exception types map outages to 503 and bad SQL to a query error.

#### [`services/telemetry.py`](./services/telemetry.py)

- **Purpose/problem solved:** Layer 8.2 — the DB-load-reduction
  number. It counts **real operations**, not HTTP requests, so N
  concurrent misses coalesced by `SingleFlight` record exactly one
  database read.
- **Class/functions:** `RequestTelemetry.record_get(source)` /
  `record_search(hit)` / `record_set()` / `record_delete()` increment
  the counters; `stats()` returns them with the derived
  `db_reads_avoided` and `db_load_reduction_pct`; `reset()` clears
  them for a demo.
- **Imports:** `threading` protects the counters; `time` stamps the
  window.

#### [`services/process_manager.py`](./services/process_manager.py)

- **Purpose/problem solved:** Layer 9.1 — spawn and stop cache-node
  OS processes for a parent process (the router scaling the cluster
  from the UI, or `run.py`). It owns processes only; membership
  bookkeeping stays in `routes_manage.py`.
- **Functions:** `project_python()` prefers the project venv
  interpreter; `process_manager.start(port, capacity)` runs
  `main.py`, `stop(port)` terminates a child it owns or kills via the
  PID file `main.py` wrote, and `stop_all()` is the router/launcher
  shutdown hook. Logs go to `data/logs/node-<port>.log`, PID files to
  `data/pids/`, so a node started by *another* process is still
  adoptable and stoppable; the `spawner` hook is injectable for tests.
- **Imports:** `os`/`sys` resolve paths and the interpreter;
  `subprocess`/`signal` start and stop children; `threading`/`time`
  implement the bounded start wait.

#### [`services/dataset_admin.py`](./services/dataset_admin.py)

- **Purpose/problem solved:** Layer 9.2 — one place owns every
  mutation a dataset can suffer from the UI and keeps four stores in
  step: database rows, node copies, `q:`/`s:` search caches and the
  replication log (so a lagging replica cannot resurrect a flushed
  row on its next sync).
- **Class/functions:** `DatasetAdmin.list()` / `get()` / `entries()`
  browse; `create()` materializes an isolated SQLite file plus its
  manifest entry; `add_entry()` derives a conflict-free key
  (`key_column` → `id` → next sequence) and upserts on repeat;
  `delete_entry()` / `delete_dataset()` cascade eviction, prefix
  flushes, search-cache purges and log drops; `after_key_change(key)`
  is the hook `PUT|DELETE /cache` and `/db` call for dataset keys, and
  `purge_search_caches()` / `_sync_stats()` keep pages and manifest
  row counts honest.
- **Imports:** `detect_schema`/`slugify` from the registry re-detect
  the schema after edits (`SCHEMA_SAMPLE` rows); `DatabaseRepository`
  reads and writes the entity's own file.

### 3.5 `router/` — HTTP API and application wiring

#### [`router/__init__.py`](./router/__init__.py)

- **Purpose/problem solved:** Provides package-level imports without
  importing the application and starting its background monitor as a side
  effect when a caller only wants a small submodule such as `router.config`.
- **Functions/behavior:** `_EXPORTS` maps public names to modules.
  `__getattr__()` lazily imports and caches a requested export;
  `__dir__()` includes those exports in introspection.
- **Imports:** `importlib` implements lazy module loading.

#### [`router/config.py`](./router/config.py)

- **Purpose/problem solved:** Centralizes node URLs and cluster/database/
  recovery/warm-up settings, including environment overrides and URL cleanup.
- **Functions/constants:** Defines defaults for nodes, virtual nodes,
  replication, health checks, timeouts, lifecycle, warm-up, and DB path.
  `normalize_node()` accepts full URLs or bare ports and normalizes local
  addresses. `nodes_from_env()` parses `CACHE_NODES`.
- **Imports:** `os` reads environment settings; `urlparse` parses and
  normalizes node URLs.

#### [`router/models.py`](./router/models.py)

- **Purpose/problem solved:** Validates the request body for cache writes.
- **Classes:** `CacheEntry` requires a `value` and accepts an optional TTL.
- **Imports:** `BaseModel` performs Pydantic validation; `Any` allows general
  JSON-compatible values; `Optional` types the optional TTL.

#### [`router/app.py`](./router/app.py)

- **Purpose/problem solved:** Creates the FastAPI application object and API
  metadata.
- **Symbols:** `app` is the application to which route modules attach
  endpoints.
- **Imports:** `FastAPI` creates the ASGI application.

#### [`router/_components.py`](./router/_components.py)

- **Purpose/problem solved:** Constructs and wires the router's shared
  dataset registry, multi-dataset repository, cluster manager, data-plane
  client, cache service, search service, hot-key manager, and warm-up
  service.
- **Symbols:** Module-level instances represent the production process.
  `configure_cluster()` rebuilds cluster-dependent components for tests and
  restarts, and updates the compatibility shim's references.
- **Imports:** `sys` allows the shim reference update. The cache, cluster,
  database, and service imports supply the shared components; settings and
  `nodes_from_env()` supply their configuration.
- **Operational note:** Importing this module constructs the repository and
  starts the health monitor. The package initializer avoids doing so until
  these exports are requested.

#### [`router/routes_cache.py`](./router/routes_cache.py)

- **Purpose/problem solved:** Exposes cache-aside GET, database-first PUT,
  and database-plus-cache DELETE through HTTP.
- **Endpoints:** `get_cache()` maps missing keys to 404 and storage failures
  to 503; `set_cache()` validates through `CacheEntry`, persists then caches,
  and reports when no cache node accepted the write; `delete_cache()` maps
  errors/missing data and reports deletion status.
- **Imports:** `HTTPException` maps service failures to HTTP status codes;
  `DatabaseUnavailableError` distinguishes storage outages; `app`,
  `CacheEntry`, and `_components` connect the endpoints to application,
  validation, and shared services.

#### [`router/routes_db.py`](./router/routes_db.py)

- **Purpose/problem solved:** Offers explicit source-of-truth endpoints for
  diagnostic or direct database reads/deletes that bypass cache-aside.
- **Endpoints:** `db_get()` reads a database value; `db_delete()` deletes
  from SQLite only. Both map missing records to 404 and storage failures to
  503.
- **Imports:** `HTTPException`, `DatabaseUnavailableError`, `app`, and
  `_components` provide response mapping and the shared repository.

#### [`router/routes_cluster.py`](./router/routes_cluster.py)

- **Purpose/problem solved:** Exposes cluster health, lifecycle, hot-key,
  warm-up, and circuit-breaker state for operations/diagnostics.
- **Endpoints:** `cluster_status()` returns manager state.
  `cluster_hot_keys()` queries active nodes and merges their hot-key
  snapshots. `cluster_warmup_status()` returns settings and per-node warm-up
  state. `cluster_warmup_trigger()` checks membership and requests warm-up.
  `circuit_breaker_status()` exposes breaker metrics; `health()` reports
  router health.
- **Imports:** `requests` queries node statistics; `HTTPException` and
  `Query` validate/translate HTTP inputs; `app` registers routes; `_components`
  provides the shared cluster/services.

#### [`router/routes_static.py`](./router/routes_static.py)

- **Purpose/problem solved:** Serves the web UI from disk on every
  request and sends the bare root URL to it.
- **Behavior:** mounts `app.mount("/ui", StaticFiles(directory=FRONTEND_DIR,
  html=True))` **last on purpose** — Starlette matches in registration
  order, so `/cache`, `/search`, `/dashboard`, `/cluster` and `/db`
  always win and the static mount only answers what they do not. A
  missing frontend directory prints a warning instead of blocking API
  boot, and `root()` answers `GET /` with a redirect to `/ui`.
- **Imports:** `os` checks the directory; `FRONTEND_DIR` (env
  `FRONTEND_DIR`) locates it; `RedirectResponse` and `StaticFiles`
  implement the two behaviors.

#### [`router/routes_search.py`](./router/routes_search.py)

- **Purpose/problem solved:** Layer 8.1 HTTP surface for cached
  search and prefix browsing.
- **Endpoints:** `GET /search/entities` returns the per-entity
  filters, sortable columns and suggest fields the UI renders;
  `GET /search/{entity}` runs one cached query combination (response
  carries `source`, `cache_key`, `took_ms`, `db_took_ms`);
  `GET /search/{entity}/suggest` serves the separately cached `s:`
  suggestions; `GET /browse/prefix` pages any key prefix (dataset
  keys or the cached `q:` result sets) for the Inspector.
- **Imports:** `Request` reads the query string; `HTTPException`
  maps a bad/unknown entity to 422, unexpected errors to 500 and a
  database outage to 503; `_components.search_service` does the work.

#### [`router/routes_dashboard.py`](./router/routes_dashboard.py)

- **Purpose/problem solved:** Aggregates everything the Dashboard tab
  shows into one call, plus the telemetry reset used before demos.
- **Endpoints:** `GET /dashboard/aggregate` folds per-node lifetime
  and windowed stats, router telemetry (`db_reads`,
  `db_reads_avoided`, `db_load_reduction_pct`), circuit-breaker and
  replication state, hot keys and cluster size; `POST
  /dashboard/telemetry/reset` zeroes the counters.
- **Imports:** `requests` fans out to each node's `/stats` with a
  short timeout and reports a dead node inline (one broken panel, not
  a failed request); `_components` supplies telemetry, breaker and
  replication state.

#### [`router/routes_manage.py`](./router/routes_manage.py)

- **Purpose/problem solved:** Layer 9.1 — cluster control from the
  UI: which processes run where, and the ability to change that.
- **Endpoints:** `GET /cluster/nodes` returns membership plus process
  ownership (`owned`/`attached`/`stopped`), PID, state and cache
  stats; `POST /cluster/nodes {"target": N}` scales (grow spawns from
  the pool, shrink stops and deregisters the highest ports, an equal
  target starts any dead member); `POST /cluster/nodes/{port}/start|`
  `stop|restart` manage one pool port (400 outside the pool).
- **Behavior:** membership bookkeeping lives here, process mechanics
  in `services/process_manager.py`. A freshly registered node joins
  as `STARTING` and is driven by the existing health monitor through
  `RECOVERING` → warm-up/seeding → `READY`; `_wait_healthy()` gives a
  scale-up `NODE_START_TIMEOUT` seconds to answer `/health`.
- **Imports:** `ScaleRequest` (Pydantic) validates the target;
  `check_node` probes ports; `process_manager` spawns/stops;
  `HTTPException`/`Path` map bad ports and start failures.

#### [`router/routes_node.py`](./router/routes_node.py)

- **Purpose/problem solved:** Direct per-node cache access for the UI
  (Lab 5's live LRU view) — the client API fans out through the hash
  ring, so without these routes nobody can watch one node's linked
  list reorder.
- **Endpoints:** `GET /cluster/nodes/{port}/cache-state` (proxies the
  node's `GET /stats/lru`), `GET|PUT|DELETE /cluster/nodes/{port}/
  cache/{key}` and `DELETE /cluster/nodes/{port}/cache-prefix/
  {prefix:path}` (proxied to `/internal/prefix`). No fan-out, no
  replication, no database — lab traffic stays local to that node.
- **Behavior:** `_target()` rejects any port outside
  `NODE_PORT_BASE..+MAX_NODES-1` (400) so the proxy can never be
  aimed at an arbitrary host; an unreachable node is 502; `_key_path()`
  re-encodes keys so every character survives the path segment.
- **Imports:** `requests` performs the one forward call; `Path`
  validates the port; `CacheEntry` validates writes; `config` supplies
  the pool bounds and timeout.

#### [`router/routes_datasets.py`](./router/routes_datasets.py)

- **Purpose/problem solved:** Layer 9.2 — dataset management from the
  web UI; this module only translates HTTP, all mutation logic lives
  in `services/dataset_admin.py`.
- **Endpoints:** `GET|POST /datasets`, `GET|DELETE /datasets/{name}`,
  `GET|POST /datasets/{name}/entries` and
  `DELETE /datasets/{name}/entries?key=...` (paged browse, add or
  upsert one row, delete one row everywhere).
- **Imports:** `DatasetCreate`/`EntryCreate` Pydantic models validate
  bodies; `HTTPException` maps bad input to 400, unknown
  dataset/key to 404 and storage outages to 503.

#### [`router/router.py`](./router/router.py)

- **Purpose/problem solved:** Preserves the established `router.router:app`
  import path and re-exports the application and component globals.
- **Behavior:** Imports each route module so its decorators register
  endpoints on `app`; defines its public compatibility exports.
- **Imports:** `app` and the shared components are re-exported; the route
  modules are imported for their endpoint-registration side effects.

### 3.6 `frontend/` — the web UI

Vanilla HTML/CSS/JavaScript single-page app with **no build step**. The
router mounts it from disk on every request
(`router/routes_static.py` → `app.mount("/ui", StaticFiles(...))`), so
an edit to any file here is live on the next page load — no server
restart. `GET /` is only a redirect to `/ui`.

#### [`frontend/index.html`](./frontend/index.html)

- **Purpose/problem solved:** The entire SPA markup: the header with
  the live cluster pill, one `<section>` per view, and every form,
  table, chart container and stat card the scripts fill in. The six
  views are `view-search`, `view-dashboard`, `view-cluster`,
  `view-datasets`, `view-inspector` and `view-lab`.
- **Important behavior:** Element ids are the contract between the
  markup and the scripts — 95 ids in total and every one is accounted
  for: 89 appear literally in the JavaScript, and the six view-section
  ids (`view-search`, `view-dashboard`, `view-cluster`,
  `view-datasets`, `view-inspector`, `view-lab`) are addressed by the
  router's `` `view-${name}` `` lookup. Each view opens with a
  `.view-head` title and a one-line description, and the Lab forms use
  labelled `.form-row` / `.field` groups.

#### [`frontend/style.css`](./frontend/style.css)

- **Purpose/problem solved:** The whole visual system: palette tokens,
  page layout, per-view styles, cards, tables, forms, badges, the lab
  chart and the responsive rules.
- **Important behavior:** Every colour is declared once as a CSS custom
  property under `:root`; the SVG donut/bar chart drawn in `app.js`
  repeats those same hex values, so a palette change has to be made in
  both places.

#### [`frontend/app.js`](./frontend/app.js)

- **Purpose/problem solved:** The application shell plus two of the six
  views. It provides `el()` (DOM builder), `api()` (fetch wrapper),
  `esc`/`fmtNum`/`debounce`/`setVerdict` helpers, the `state` object,
  the view router (`registerView` / `showView`), the cluster pill
  refresh, the dataset-entity catalogue, and `init()` which wires the
  tabs and forms. It also implements the **Search** view
  (`runSearch`, facets, result cards, pager) and the **Dashboard**
  view (`renderStatCards`, `renderHitRateBars`, `renderDonut`,
  `renderHotKeys`, `renderNodeStates`, `renderBreakerRepl`).
- **Important behavior:** `el(tag, attrs, children)` passes real `Node`
  children straight through and stringifies anything else, so numbers
  and booleans can be handed to it directly.

#### `frontend/views/*.js`

One module per remaining view; each calls `registerView(name, hooks)`
so the shell can show/hide it:

- [`cluster.js`](./frontend/views/cluster.js) — cluster sizing form and
  the per-node cards (state, process ownership, PID, cache stats and
  start/stop/restart buttons), refreshed every 3 s.
- [`datasets.js`](./frontend/views/datasets.js) — dataset list, detail
  pane, entry browsing, add-entry form and the row/dataset deletions;
  it re-runs `reloadEntities()` after a mutation so the Search tab's
  entity picker stays in step.
- [`inspector.js`](./frontend/views/inspector.js) — single-key lookup
  and prefix browsing, including the split **Evict from cache** /
  **Delete from database** actions and the consistency verdict. It is
  also reused from the Search tab's "Inspect key" links.
- [`lab.js`](./frontend/views/lab.js) — the five labs: search storm,
  point-read storm, node-failure drill, eviction burst and the live
  LRU-eviction watcher (which polls a node's `/stats/lru` and animates
  insert / touch / evict transitions).

### 3.7 Root-level Python files — entry points and data tools

#### [`main.py`](./main.py)

- **Purpose/problem solved:** Runs one independent in-memory cache node as an
  HTTP service.
- **Functions/endpoints:** `parse_args()` handles port/capacity CLI options.
  `CacheEntry` validates normal writes and `ReplicationEntry` validates
  the sequenced `/internal/replicate` body. `/health`, `/cache/{key}`
  GET/PUT/DELETE, `/cache/{key}/exists`, `/stats`, `/stats/window`,
  `/stats/top-keys` and `/stats/lru` expose cache operations and
  node-local statistics; `/internal/replicate`, `/internal/entry`,
  `/internal/prefix` and `/internal/shutdown` serve replication,
  prefix purge and graceful stop. The `__main__` block creates a
  configured cache/statistics instance, starts rotation, writes the
  PID file that lets another process find and stop this node later,
  and launches Uvicorn.
- **Imports:** `argparse` parses CLI settings; `uvicorn` runs the server;
  FastAPI's `FastAPI`, `HTTPException`, and `Query` define the API and
  responses; Pydantic `BaseModel`/`Field` validate values and positive TTL;
  `Any`/`Optional` type request values; `LRUCache` and `CacheStatistics`
  provide cache and telemetry implementations.
- **Important behavior:** Cache storage is process memory and is lost when
  the node exits.

#### [`run.py`](./run.py)

- **Purpose/problem solved:** One-command local launcher: optionally
  seeds the drop folder, spawns the cache nodes, and runs the router
  in-process so a single Ctrl+C stops the whole cluster.
- **Functions:** `parse_args()` defines `--nodes`, `--port`,
  `--capacity`, and `--seed`. `seed_drop_folder()` imports new or
  changed files from `datasets/` before boot and prints the seeder's
  per-file report. `spawn_nodes()` starts `count` nodes from the port
  pool through the shared `process_manager` (adopting a port that
  already answers `/health` instead of double-starting).
  `main()` orchestrates and hands off to `uvicorn.run`, with a
  `KeyboardInterrupt` guard for a clean exit.
- **Imports:** `argparse`/`os`/`sys`/`time` handle CLI parsing, paths,
  exit codes, and the brief pre-server settle delay; `uvicorn` serves
  the router in this process; `router.config` supplies `NODE_CAPACITY`
  and `NODE_PORT_BASE`; `cluster.health_monitor.check_node` probes
  candidate ports; `services.process_manager.process_manager` spawns
  and stops nodes (its PID files let the web UI stop them too);
  `import_dataset.seed_drop_folder` performs the drop-folder import.
- **Engineering note:** Because the router runs in this process, its
  lifespan hook (`process_manager.stop_all`) is the same registry the
  launcher spawned into — Ctrl+C leaves no orphaned nodes.

#### [`import_dataset.py`](./import_dataset.py)

- **Purpose/problem solved:** Imports one external data file (Kaggle,
  OpenML, data.gov, ...) into its own SQLite database and registers it,
  so every file becomes one searchable entity with no hardcoded schema.
- **Functions/constants:** `build_parser()`/`parse_args()`/`main()` expose
  the CLI: `--file`, `--name`, `--prefix`, `--key-column`, `--limit`,
  `--dry-run`, `--reimport`, `--seed` (import the drop folder),
  `--drop-dir`, `--list`, and `--delete`. `detect_format()` maps extensions
  (CSV, TSV, JSON, JSONL/NDJSON, Parquet, Excel, Zip) to a reader;
  `read_rows()` streams records (unzipping Zip containers and delegating
  Parquet/Excel to pandas); `entry_from_record()` splits a record into
  `<prefix><key-column value>` and the remaining columns as a JSON value
  (sanitized keys keep their column; row numbers are the fallback);
  `import_file()` sniffs the key column and schema through the registry,
  writes rows with `set_many()` into a temporary `.import` file and swaps
  it in with `os.replace` so a failed import never destroys existing data,
  then registers or re-imports the dataset. `seed_drop_folder()` imports
  every new or changed file in the drop folder and returns per-file
  `imported`/`reimported`/`failed` records (used by `run.py --seed`).
  `list_datasets()` reports live row counts and database sizes.
- **Imports:** `argparse`/`sys` provide CLI and error reporting; `csv`,
  `json`, `zipfile`, and `os` read inputs and manage paths; `time` measures
  elapsed import time. `DatasetRegistry` owns the manifest; the repository
  classes provide batched persistence.

### 3.8 `tests/` — the automated suite

23 test modules, **377 tests** in total (the per-module counts below
are the same ones the README and `TESTING_GUIDE.md` break out).
Everything under `tests/` is tracked in Git — the suite is part of
the deliverable, not scratch work.

#### [`tests/conftest.py`](./tests/conftest.py)

- **Purpose/problem solved:** Makes pytest hermetic. The router builds
  its `DatabaseRepository` from `CACHE_DB_PATH` **at import time** and
  several router fixtures call `database.clear()` / `delete()`, so
  without this file every run would operate on the real `data/cache.db`
  and wipe it.
- **Behavior:** Creates `tempfile.mkdtemp(prefix="dcs-tests-")` and
  points `CACHE_DB_PATH` at a throwaway `cache.db` inside it *before
  any test module imports the router*; an `atexit` hook removes the
  directory afterwards (`shutil.rmtree(..., ignore_errors=True)`).
- **Imports:** `os`/`tempfile` build the path; `atexit`/`shutil`
  guarantee cleanup even when the suite fails.

#### [`tests/__init__.py`](./tests/__init__.py)

- **Purpose:** Empty package marker so the test modules form one
  importable package.

#### Test modules by concern

| Module | Tests | What it pins down |
|---|---|---|
| `test_cache.py` | 16 | LRU engine: eviction, TTL, stats, `delete_prefix`, `get_or_set` stampede protection |
| `test_consistent_hash.py` | 16 | Ring building, virtual nodes, replica selection with distinct physical nodes |
| `test_statistics.py` | 15 | Counters, window rotation, bounded hot-key tracking, concurrency |
| `test_node_lifecycle.py` | 28 | `STARTING/RECOVERING/READY/UNHEALTHY/FAILED` transitions and gating |
| `test_warmup.py` | 28 | Throttled warm-up, ownership, rejoin gate, DB-outage behavior, seeding integration |
| `test_seeding.py` | 18 | Layer 6.5 candidate selection: popularity ordering, interleaving, budget, fallback |
| `test_replication.py` | 37 | Sequence allocation, bounded log, acks, incremental/snapshot sync, failover, repair |
| `test_repository.py` | 20 | SQLite CRUD, JSON round-trip, read-only SQL inspection |
| `test_multi_repository.py` | 18 | Prefix routing across the per-dataset files |
| `test_registry.py` | 21 | Manifest, schema/key sniffing, drop-folder scan, reimport bookkeeping |
| `test_import_dataset.py` | 29 | Formats, duplicate-key collapse, atomic `.import` swap, CLI flags |
| `test_cache_service.py` | 13 | Cache-aside flow (hit/miss/set/delete), coalesced concurrent misses, DB-outage behavior, breaker opening |
| `test_circuit_breaker.py` | 11 | `CLOSED` → `OPEN` → `HALF-OPEN` state machine |
| `test_single_flight.py` | 5 | Leader/follower coalescing of concurrent misses |
| `test_config.py` | 6 | Node URL/port pool parsing from `CACHE_NODES` |
| `test_router.py` | 9 | Router endpoints end-to-end (cache, `/db`, cluster status) |
| `test_manage_routes.py` | 9 | Scale / start / stop / restart cluster-control routes |
| `test_datasets_routes.py` | 12 | Dataset and entry CRUD routes, incl. error mapping |
| `test_process_manager.py` | 12 | Spawn, stop, adopt, log/PID file handling |
| `test_search.py` | 39 | Cached search: filters, ranges, sort, pagination, suggest, browse |
| `test_telemetry.py` | 7 | DB-load-reduction counters and derived metric |
| `test_dashboard.py` | 4 | `/dashboard/aggregate` shape and telemetry reset |
| `test_lru_lab.py` | 4 | Lab 5's per-node LRU view and control calls |

- **Imports:** Not applicable (they exercise every package above).

### 3.9 Project metadata and documentation

#### [`.gitignore`](./.gitignore)

- **Purpose/problem solved:** Keeps local runtime artifacts out of Git:
  virtual environments, Python bytecode, test/coverage output, IDE/OS files,
  logs, SQLite data (system store, `data/datasets/*.db`, registry), user
  dataset files in the `datasets/` drop folder, research PDFs, and the
  capstone report work — `DCS_Capstone_Report_*`, `PersonalLLM_*` and the
  whole `_report_bench/` tree (bench harness, generated figures, UI
  screenshots, JSON results). The test suite itself is deliberately
  **not** ignored: `tests/` is tracked.
- **Imports:** Not applicable.

#### [`requirements.txt`](./requirements.txt)

- **Purpose/problem solved:** Lists install-time Python packages used by
  application and development commands.
- **Dependencies:** `fastapi` defines HTTP APIs; `uvicorn` serves ASGI
  applications; `requests` performs node/router HTTP calls; `pytest` is the
  documented test runner.

#### [`requirements-import.txt`](./requirements-import.txt)

- **Purpose/problem solved:** Optional dependencies needed only to
  import Parquet and Excel dataset files (pandas, pyarrow, openpyxl).
  CSV/TSV/JSON/JSONL imports use the standard library alone;
  `import_dataset.py` reports a clear error if a Parquet/Excel file
  arrives without these installed.
- **Imports:** Not applicable.
- **Note:** Versions are unpinned.

#### [`README.md`](./README.md)

- **Purpose/problem solved:** Gives the project overview, architecture,
  layer roadmap, quick start, configuration, and top-level testing/import
  commands.
- **Important content:** Describes the source-of-truth/cache relationship,
  the dataset import flow (drop folder → per-dataset SQLite → registry),
  the development roadmap, and where the (untracked) capstone report
  artifacts live.
- **Imports:** Not applicable.

#### [`commands.md`](./commands.md)

- **Purpose/problem solved:** Detailed operator/developer command reference:
  environment setup, the launcher, layer-by-layer endpoints and demos
  (including cold-start seeding and the Layer 7 replication/sync
  surface), fault-tolerance walkthroughs, dataset import, cluster
  control and per-node helpers, the Layer 8/9 web-UI endpoints, and
  test commands.
- **Important content:** Contains setup and examples; it is not executable
  application logic. Cross-check these commands against the current code
  before treating them as operational guarantees.
- **Imports:** Not applicable.

#### [`LICENSE`](./LICENSE)

- **Purpose/problem solved:** States the project's Apache License 2.0
  permissions and conditions.
- **Imports:** Not applicable.

#### [`PROJECT_GUIDE.md`](./PROJECT_GUIDE.md)

- **Purpose/problem solved:** This internal file inventory and architectural
  reference. It explains the implementation and its current limitations.
- **Imports:** Not applicable.

#### [`TESTING_GUIDE.md`](./TESTING_GUIDE.md)

- **Purpose/problem solved:** The team's phased test plan: Phase 0 runs
  the automated suite, then manual smoke phases cover the dataset
  pipeline, a single node, the router cluster, failure/rejoin, the
  circuit breaker, warm-up, import, replication and the web UI —
  followed by a 61-row master checklist and a troubleshooting table.
- **Imports:** Not applicable.

#### Capstone report artifacts (untracked)

- **Purpose/problem solved:** The written report for this project:
  `DCS_Capstone_Report_*.docx` / `DCS_Capstone_Report_*.pdf` in the
  repository root, older `PersonalLLM_*` drafts, and the
  `_report_bench/` tree that produced them — bench/soak harness,
  figure generation, UI screenshots, validation scripts and their
  JSON results.
- **Important content:** Derived material only; none of it is
  committed (see the `.gitignore` entry above). When the report and
  the code disagree, the code and `tests/` are authoritative and the
  report is re-exported.
- **Imports:** Not applicable.

## 4. Runtime configuration and externally visible surfaces

### Main configuration

| Setting | Default in code | Purpose |
|---|---|---|
| `CACHE_NODES` | Three `127.0.0.1` URLs on ports 8001–8003 | Router node membership |
| `CACHE_DB_PATH` | `data/cache.db` | System SQLite store; the registry (`data/registry.json`) and per-dataset files (`data/datasets/*.db`) derive from its directory |
| `RECOVERY_THRESHOLD` | `2` | Consecutive healthy checks before readiness |
| `CLUSTER_BOOTSTRAP_STATE` | `starting` | Initial state for configured nodes |
| `WARMUP_ENABLED` | enabled | Enables recovery warm-up/readiness gate |
| `WARMUP_LIMIT` | `1000` | Number of top hot keys considered |
| `WARMUP_RATE_LIMIT` | `100` reads/second | Shared warm-up DB-read throttle |
| `HOTKEY_MAX_KEYS` | `10000` | Router-side distinct-key bound |
| `SEED_ENABLED` | enabled | Layer 6.5 cold-start seeding (consulted only while the hot-key ranking is empty) |
| `SEED_STRATEGY` | `popular` | Seed selection: top rows per dataset's popularity column, or a uniform sample |
| `SEED_FILL_RATIO` | `0.6` | Fraction of a node's own capacity seeding may claim |
| `SEED_PER_DATASET` | `250` | Candidate rows pulled per dataset |
| `SEED_FALLBACK_CAPACITY` | `100` | Capacity assumed for a node that does not answer `/stats` |
| `MAX_NODES` | `8` | Node port pool the UI may spawn into |
| `NODE_PORT_BASE` | `8001` | First port of that pool |
| `NODE_CAPACITY` | `5000` | Capacity of nodes spawned by the router |
| `NODE_START_TIMEOUT` | `10` seconds | How long a scale/start request waits for `/health` |
| `REPLICATION_ENABLED` | enabled | Layer 7 log, per-node acks and sync |
| `REPLICATION_LOG_SIZE` | `10000` | Bound on the in-memory replication log |
| `SYNC_SNAPSHOT_THRESHOLD` | `1000` | Sequence gap that forces a snapshot resync instead of log replay |
| `SEARCH_TTL` | `300` seconds | Lifetime of a cached `q:`/`s:` search entry |
| `SEARCH_PAGE_SIZE` | `20` | Default search page size |
| `SEARCH_PAGE_SIZE_MAX` | `100` | Hard cap on the search page size |
| `FRONTEND_DIR` | `frontend/` | Directory the `/ui` static mount serves |

Other code defaults include 100 virtual nodes, replication factor 2, health
interval 2 seconds, failure threshold 3, and request timeout 2 seconds.

### HTTP surface

**Cache node (`main.py`, typically ports 8001–8003):**

- `GET /health`
- `GET /cache/{key}`
- `PUT /cache/{key}` with `{ "value": ..., "ttl": ... }`
- `DELETE /cache/{key}`
- `GET /cache/{key}/exists`
- `GET /stats`, `/stats/window`, `/stats/top-keys`
- `GET /stats/lru` — the recency-ordered key list (MRU → LRU) the
  Lab tab watches
- Node-internal (normally driven by the router): `PUT|DELETE
  /internal/replicate/{key}` (apply one sequenced mutation),
  `GET /internal/entry/{key}` (value + sequence),
  `DELETE /internal/prefix/{prefix:path}` (drop a whole prefix) and
  `POST /internal/shutdown` (graceful exit — what the Cluster tab's
  *stop* calls)

**Router (`router/routes_*.py`, typically port 8000):**

- `GET /` — redirect to `/ui`; the `/ui` mount serves `frontend/` from
  disk on every request, so UI edits need no restart.
- `GET /health`
- **Cache / database:** `GET|PUT|DELETE /cache/{key}` (the `DELETE`
  only **evicts** node copies) and `GET|DELETE /db/{key}` (direct
  database access; its `DELETE` removes the row everywhere).
- **Search (8.1):** `GET /search/entities`, `GET /search/{entity}`,
  `GET /search/{entity}/suggest`, `GET /browse/prefix`.
- **Telemetry (8.2):** `GET /dashboard/aggregate`,
  `POST /dashboard/telemetry/reset`.
- **Cluster introspection:** `GET /cluster/status`,
  `GET /cluster/hot-keys`, `GET /cluster/warmup`,
  `POST /cluster/warmup/{node:path}`, `GET /circuit-breaker/status`.
- **Replication (7.3–7.6):** `GET /cluster/replication`,
  `POST /cluster/resync/{node:path}`.
- **Cluster control (9.1):** `GET|POST /cluster/nodes` and
  `POST /cluster/nodes/{port}/start|stop|restart`, plus the per-node
  cache helpers `GET /cluster/nodes/{port}/cache-state`,
  `GET|PUT|DELETE /cluster/nodes/{port}/cache/{key}` and
  `DELETE /cluster/nodes/{port}/cache-prefix/{prefix:path}`.
- **Dataset admin (9.2):** `GET|POST /datasets`,
  `GET|DELETE /datasets/{name}` and
  `GET|POST|DELETE /datasets/{name}/entries`.

FastAPI also provides its normal OpenAPI/docs surfaces when enabled.

## 5. Engineering notes and limitations

These notes describe observed code behavior, not future design intent:

1. **Prototype, not a production deployment recipe.** The project is
   educational and runs a single router process with local SQLite by default.
   The code shown here does not implement authentication/authorization or a
   TLS deployment configuration.
2. **Not strongly consistent replication.** Writes are fanned out to
   the selected nodes without a quorum: a `PUT` reports `replicated_to`
   and lists the nodes that accepted it. When *no* node accepted the
   write, the API still answers HTTP 200 with a `warning` and
   `source: "database"` — a scaled-down cluster is a legitimate state
   and the database row is the source of truth (Layer 9). The Layer
   7.3 log is a bounded **in-memory** `deque`, so it is not a durable
   journal: a router restart loses both the sequence history and the
   per-node ack marks. Only a database write failure surfaces as
   HTTP 503; cache fan-out is best-effort.
3. **Cache TTL and database retention differ.** Expiration removes/ignores an
   in-memory entry; the SQLite value remains until explicitly deleted.
4. **Cache misses treat `None` as absent.** Both the cache and service use
   `None` to represent a miss, so a stored JSON `null` is not distinguishable
   from a miss through the current GET path.
5. **Single-flight and telemetry are process-local.** Multiple router
   processes would not share in-flight requests or the router's hot-key
   ranking.
6. **SQL inspection is not safely read-only.** The
   `DatabaseRepository.query()` method checks that a single statement begins
   with one of four allowed keywords and limits result rows, but
   `_get_connection()` opens a regular SQLite connection; it does not use
   `mode=ro`. A mutating statement can be prefixed by `WITH`, and `PRAGMA`
   can also have side effects. Today the helper is only called from
   `services/seeding.py` with trusted SQL and is **not** exposed over HTTP —
   do not wire it up to an untrusted caller without switching the
   connection to `file:...?mode=ro`.
7. **The per-node cache proxy deliberately bypasses the data plane.**
   `PUT|DELETE /cluster/nodes/{port}/cache/{key}` (and Lab 5's direct
   node calls) touch exactly one node's RAM with no database write and
   no replication — that is precisely what makes a deterministic LRU
   demonstration possible. Aimed at application data it creates the
   divergence the Inspector reports as `STALE`/`PARTIAL`, so treat
   these routes as lab and cleanup tooling, not as a write path.

## 6. Suggested reading order for maintainers

1. [`cache/lru_cache.py`](./cache/lru_cache.py) and
   [`cache/_linked_list_ops.py`](./cache/_linked_list_ops.py) for the local
   data structure.
2. [`cluster/consistent_hash.py`](./cluster/consistent_hash.py),
   [`cluster/node_state.py`](./cluster/node_state.py),
   [`cluster/node_lifecycle.py`](./cluster/node_lifecycle.py), and
   [`cluster/node_manager.py`](./cluster/node_manager.py) for placement and
   node lifecycle.
3. [`database/repository.py`](./database/repository.py) and its mixins for
   persistence.
4. [`services/distributed_cache.py`](./services/distributed_cache.py),
   [`services/cache_service.py`](./services/cache_service.py),
   [`services/single_flight.py`](./services/single_flight.py), and
   [`services/circuit_breaker.py`](./services/circuit_breaker.py) for
   request behavior.
5. [`services/warmup.py`](./services/warmup.py) and
   [`cache/hot_keys.py`](./cache/hot_keys.py) for recovery warm-up.
6. [`router/_components.py`](./router/_components.py) and the route modules
   for application wiring and HTTP contracts.
7. [`main.py`](./main.py), [`run.py`](./run.py), and
   [`import_dataset.py`](./import_dataset.py) for local execution and
   tooling.
