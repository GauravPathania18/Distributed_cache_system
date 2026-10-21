# Distributed Cache System — Engineering Reference

> Internal project documentation for engineers. This describes the code
> currently present in the repository; it is not a product or end-user guide.

## 1. Repository scope

This guide covers the **48 files tracked by Git** at the time it was written:
43 Python files and 5 project-level metadata/documentation files. It describes
the purpose of each file, the problems it addresses, its principal classes or
functions, and why its imports are needed.

Generated data, local environments, research materials, and test files are
excluded from this tracked-file inventory. In particular, `.gitignore`
excludes `tests/`, `data/`, `datasets/`, `.venv/`, and `/DCS_research`. The
README and `commands.md` describe a pytest suite, but no test files are tracked
in the current repository inventory. Treat their stated test counts as
documentation claims, not as verification from this checkout.

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
- A terminal launcher/control panel and a file-based dataset importer.

### Request and recovery paths

**GET:** client → router `CacheService` → distributed cache (primary followed
by replicas) → on miss, single-flight → circuit breaker → SQLite → populate
cache. A cache hit does not require a database call.

**PUT:** router → SQLite first → cache nodes chosen by the hash ring. The HTTP
API reports failure if no cache node accepted the value, even though the
database write may already have succeeded.

**DELETE:** router → SQLite delete → invalidate selected cache nodes.

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
- Replication in the current implementation is a fan-out of an operation to
  the distinct nodes returned by `NodeManager`. There is no replica quorum,
  version vector, replication log, or anti-entropy repair protocol.
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
  `repos_matching()`, and `repos()` expose the individual stores for the
  TUI and SQL console; `query()` infers the target store from `LIKE`
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

#### [`router/router.py`](./router/router.py)

- **Purpose/problem solved:** Preserves the established `router.router:app`
  import path and re-exports the application and component globals.
- **Behavior:** Imports each route module so its decorators register
  endpoints on `app`; defines its public compatibility exports.
- **Imports:** `app` and the shared components are re-exported; the route
  modules are imported for their endpoint-registration side effects.

### 3.6 Root-level Python files — entry points and data tools

#### [`main.py`](./main.py)

- **Purpose/problem solved:** Runs one independent in-memory cache node as an
  HTTP service.
- **Functions/endpoints:** `parse_args()` handles port/capacity CLI options.
  `CacheEntry` validates writes. `/health`, `/cache/{key}` GET/PUT/DELETE,
  `/cache/{key}/exists`, `/stats`, `/stats/window`, and `/stats/top-keys`
  expose cache operations and node-local statistics. The `__main__` block
  creates a configured cache/statistics instance, starts rotation, and
  launches Uvicorn.
- **Imports:** `argparse` parses CLI settings; `uvicorn` runs the server;
  FastAPI's `FastAPI`, `HTTPException`, and `Query` define the API and
  responses; Pydantic `BaseModel`/`Field` validate values and positive TTL;
  `Any`/`Optional` type request values; `LRUCache` and `CacheStatistics`
  provide cache and telemetry implementations.
- **Important behavior:** Cache storage is process memory and is lost when
  the node exits.

#### [`tui.py`](./tui.py)

- **Purpose/problem solved:** One-command local launcher and interactive
  operations panel for the router and cache nodes.
- **Functions:** `node_port()`, `db_path()`, and `repository()` resolve local
  endpoints/storage (`repository()` returns a `MultiDatasetRepository`).
  `auto_seed()` imports new/changed files from the `datasets/` drop folder
  before the cluster starts. `init_color()`, `paint()`, and `clear()` render
  the terminal. `http()`/`_detail()` normalize API results.
  `project_python()`, `spawn()`, `is_alive()`, `alive_children()`,
  `ensure_running()`, `stop_all()`, and `_emergency_stop()` manage child
  processes while avoiding termination of services the UI did not start.
  `wait_for()`, `active_nodes()`, `boot()`, and `print_cluster_summary()`
  coordinate startup. `header()`, `menu()`, `ask()`, `pause()`, `fmt()`, and
  `render_table()` support display/input. `parse_value()` and
  `compute_verdict()` handle values and cache-vs-database consistency
  summaries. `action_status()`, `action_put()`, `action_get()`,
  `action_delete()`, `action_db_get()`, `action_resume()`,
  `action_inspect()`, `action_list_keys()`, `action_sql()`,
  `action_stats()`, and `action_datasets()` (list/import/delete datasets)
  implement menu operations. `main()` runs the menu and arranges shutdown.
- **Imports:** Standard-library `atexit`, `signal`, `subprocess`, `sys`, and
  `os` manage child processes, shutdown, paths, and terminal behavior;
  `json` formats and parses values; `time` implements wait/deadline logic;
  `quote` and `urlparse` encode keys and parse node URLs. `requests` performs
  HTTP calls; `check_node` probes cache nodes; `DatasetRegistry` and
  `MultiDatasetRepository` provide dataset-aware diagnostics (key listing
  and the per-store SQL console); `slugify`, `import_file`,
  `list_datasets`, and `seed_drop_folder` implement dataset management;
  `DatabaseUnavailableError` classifies read failures; `nodes_from_env`
  uses the router's configured node set.
- **Engineering note:** The SQL console is intentionally described here as
  an inspection tool, but the repository query guard should not be treated
  as a security boundary; see §5.

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
  `imported`/`reimported`/`failed` records (used by `tui.py` on boot).
  `list_datasets()` reports live row counts and database sizes.
- **Imports:** `argparse`/`sys` provide CLI and error reporting; `csv`,
  `json`, `zipfile`, and `os` read inputs and manage paths; `time` measures
  elapsed import time. `DatasetRegistry` owns the manifest; the repository
  classes provide batched persistence.

### 3.7 Project metadata and documentation

#### [`.gitignore`](./.gitignore)

- **Purpose/problem solved:** Keeps local runtime artifacts out of Git:
  virtual environments, Python bytecode, test/coverage output, IDE/OS files,
  logs, SQLite data (system store, `data/datasets/*.db`, registry), user
  dataset files in the `datasets/` drop folder, and research PDFs.
- **Imports:** Not applicable.

#### [`requirements.txt`](./requirements.txt)

- **Purpose/problem solved:** Lists install-time Python packages used by
  application and development commands.
- **Dependencies:** `fastapi` defines HTTP APIs; `uvicorn` serves ASGI
  applications; `requests` performs node/router HTTP calls; `pytest` is the
  documented test runner.
- **Note:** Versions are unpinned.

#### [`README.md`](./README.md)

- **Purpose/problem solved:** Gives the project overview, architecture,
  layer roadmap, quick start, configuration, and top-level testing/import
  commands.
- **Important content:** Describes the source-of-truth/cache relationship,
  the dataset import flow (drop folder → per-dataset SQLite → registry),
  and the development roadmap.
- **Imports:** Not applicable.

#### [`commands.md`](./commands.md)

- **Purpose/problem solved:** Detailed operator/developer command reference:
  environment setup, terminal UI, layer-specific endpoints, fault-tolerance
  walkthroughs, warm-up, dataset import, and test commands.
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

**Router (`router/routes_*.py`, typically port 8000):**

- `GET /health`
- `GET|PUT|DELETE /cache/{key}`
- `GET|DELETE /db/{key}` (direct database diagnostic access)
- `GET /cluster/status`
- `GET /cluster/hot-keys`
- `GET /cluster/warmup`
- `POST /cluster/warmup/{node:path}`
- `GET /circuit-breaker/status`

FastAPI also provides its normal OpenAPI/docs surfaces when enabled.

## 5. Engineering notes and limitations

These notes describe observed code behavior, not future design intent:

1. **Prototype, not a production deployment recipe.** The project is
   educational and runs a single router process with local SQLite by default.
   The code shown here does not implement authentication/authorization or a
   TLS deployment configuration.
2. **Not strongly consistent replication.** Writes are sent to selected
   nodes without a quorum or durable replication log. A successful response
   can list fewer nodes than the configured replication factor as long as at
   least one node accepted the write. Database-first write failure is
   surfaced; cache fan-out failure after a successful database write can
   still return HTTP 503.
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
   can also have side effects. The `commands.md` claim that the connection is
   opened read-only does not match this implementation. Do not expose this
   query helper to untrusted callers.
7. **Documented tests are not present in the tracked tree.** `README.md` and
   `commands.md` cite 303 pytest tests, but `.gitignore` excludes `tests/`
   and `git ls-files` currently contains no test sources. Restore/track the
   intended test suite before using those counts as a release gate.
8. **Planned roadmap is not implemented by this inventory.** README items
   after Layer 6.3 (replica placement evolution, write replication log/version
   sync, formal failure detection, automatic failover, and re-replication/
   rebalancing) are listed as planned/next, not current capabilities.

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
7. [`main.py`](./main.py), [`tui.py`](./tui.py), and
   [`import_dataset.py`](./import_dataset.py) for local execution and
   tooling.
