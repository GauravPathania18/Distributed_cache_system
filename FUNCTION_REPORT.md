# DCS — Function Report

Generated inventory of every function in the project, grouped by feature.

- **Application functions:** 415
- **Test functions:** 479
- **HTTP endpoints:** 45
- **Python files:** 45 app files

**How to read the tables:** `Calls` = how many times the name appears as a call outside its own body (blank = dunder/property). `Loc` = lines in the body. `★` marks a FastAPI endpoint (registered by decorator, never called by name).


## Part 1 — Feature groups (pick a letter to remove a whole feature)

| ID | Feature | Files | Funcs | Func body LOC | File LOC |
|:--:|---|--:|--:|--:|--:|
| **A** | Core cache engine — LRU, TTL, eviction, cache stats view | 6 | 21 | 497 | 588 |
| **B** | Layer 6.2 — per-node statistics + hot-key ranking | 2 | 19 | 295 | 416 |
| **C** | Layer 3/6.1/7.4 — consistent hashing, node lifecycle, health monitor | 8 | 48 | 884 | 1138 |
| **D** | Layer 5 — SQLite storage, multi-repo, dataset registry | 7 | 55 | 1054 | 1321 |
| **E** | Layer 5.7/5.8 — circuit breaker + single-flight | 2 | 15 | 221 | 323 |
| **F** | Layer 6.3 — cache warm-up + recovery gate | 1 | 16 | 460 | 556 |
| **G** | Layer 6.5 — cold-start seeding | 1 | 11 | 222 | 298 |
| **H** | Layer 7.1–7.6 — replication log, sync, failover, re-replication | 1 | 28 | 661 | 753 |
| **I** | Layer 8.1 — search service (SQL builder + query-result cache) | 1 | 25 | 797 | 915 |
| **J** | Layer 8.2 — telemetry (DB-load-reduction metric) | 1 | 7 | 95 | 138 |
| **K** | Layer 9.1 — process manager (spawn/stop real node processes) | 1 | 13 | 222 | 292 |
| **L** | Layer 9.2/9.3 — dataset admin + deletion split | 1 | 16 | 304 | 389 |
| **M** | Core service wiring — cache-aside policy + fan-out | 3 | 14 | 376 | 504 |
| **N** | Router — cache/db/static routes | 3 | 6 | 164 | 215 |
| **O** | Router — cluster status + dashboard routes | 2 | 11 | 292 | 341 |
| **P** | Router — cluster control routes (Layer 9.1) | 1 | 14 | 295 | 379 |
| **Q** | Router — search routes (Layer 8.1) | 1 | 4 | 91 | 109 |
| **R** | Router — dataset admin routes (Layer 9.2) | 1 | 8 | 94 | 151 |
| **S** | Router — app assembly, config, component wiring | 6 | 7 | 205 | 549 |
| **T** | Cache node server | 1 | 14 | 179 | 403 |
| **U** | Dataset importer CLI | 1 | 17 | 554 | 685 |
| **V** | Terminal UI / one-command launcher | 1 | 43 | 918 | 1137 |
| **W** | Test suite | 23 | 479 | 7308 | 9165 |

**App total (excl. generator scripts):** 412 functions / 8880 func-LOC / 11494 file-LOC in 45 files

---

## Part 2 — HTTP endpoints (45) — NOT dead code

| Method | Path | Function |
|---|---|---|
| GET | `/health` | `main.py:74` `health()` |
| GET | `/cache/{key}` | `main.py:86` `get_value(key)` |
| PUT | `/cache/{key}` | `main.py:112` `set_value(key, entry)` |
| DELETE | `/cache/{key}` | `main.py:145` `delete_value(key)` |
| PUT | `/internal/replicate/{key}` | `main.py:186` `internal_replicate(key, entry)` |
| DELETE | `/internal/replicate/{key}` | `main.py:206` `internal_delete(key)` |
| GET | `/internal/entry/{key}` | `main.py:222` `internal_entry(key)` |
| DELETE | `/internal/prefix/{prefix:path}` | `main.py:254` `internal_flush_prefix(prefix)` |
| POST | `/internal/shutdown` | `main.py:277` `internal_shutdown()` |
| GET | `/cache/{key}/exists` | `main.py:297` `exists(key)` |
| GET | `/stats` | `main.py:310` `stats()` |
| GET | `/stats/window` | `main.py:320` `stats_window(limit)` |
| GET | `/stats/top-keys` | `main.py:336` `stats_top_keys(limit)` |
| GET | `/cache/{key}` | `router/routes_cache.py:11` `get_cache(key)` |
| PUT | `/cache/{key}` | `router/routes_cache.py:40` `set_cache(key, entry)` |
| DELETE | `/cache/{key}` | `router/routes_cache.py:89` `delete_cache(key)` |
| GET | `/cluster/status` | `router/routes_cluster.py:10` `cluster_status()` |
| GET | `/cluster/hot-keys` | `router/routes_cluster.py:16` `cluster_hot_keys(limit, per_node)` |
| GET | `/cluster/warmup` | `router/routes_cluster.py:79` `cluster_warmup_status()` |
| POST | `/cluster/warmup/{node:path}` | `router/routes_cluster.py:106` `cluster_warmup_trigger(node)` |
| GET | `/circuit-breaker/status` | `router/routes_cluster.py:131` `circuit_breaker_status()` |
| GET | `/cluster/replication` | `router/routes_cluster.py:141` `cluster_replication_status()` |
| POST | `/cluster/resync/{node:path}` | `router/routes_cluster.py:151` `cluster_resync(node)` |
| GET | `/health` | `router/routes_cluster.py:176` `health()` |
| GET | `/dashboard/aggregate` | `router/routes_dashboard.py:58` `dashboard_aggregate()` |
| POST | `/dashboard/telemetry/reset` | `router/routes_dashboard.py:155` `dashboard_telemetry_reset()` |
| GET | `/datasets` | `router/routes_datasets.py:42` `list_datasets()` |
| POST | `/datasets` | `router/routes_datasets.py:52` `create_dataset(body)` |
| GET | `/datasets/{name}` | `router/routes_datasets.py:66` `get_dataset(name)` |
| DELETE | `/datasets/{name}` | `router/routes_datasets.py:77` `delete_dataset(name)` |
| GET | `/datasets/{name}/entries` | `router/routes_datasets.py:95` `list_entries(name, limit, offset)` |
| POST | `/datasets/{name}/entries` | `router/routes_datasets.py:111` `add_entry(name, body)` |
| DELETE | `/datasets/{name}/entries` | `router/routes_datasets.py:133` `delete_entry(name, key)` |
| GET | `/db/{key}` | `router/routes_db.py:10` `db_get(key)` |
| DELETE | `/db/{key}` | `router/routes_db.py:38` `db_delete(key)` |
| GET | `/cluster/nodes` | `router/routes_manage.py:225` `cluster_nodes()` |
| POST | `/cluster/nodes` | `router/routes_manage.py:251` `cluster_scale(body)` |
| POST | `/cluster/nodes/{port}/start` | `router/routes_manage.py:326` `cluster_node_start(port)` |
| POST | `/cluster/nodes/{port}/stop` | `router/routes_manage.py:350` `cluster_node_stop(port)` |
| POST | `/cluster/nodes/{port}/restart` | `router/routes_manage.py:357` `cluster_node_restart(port)` |
| GET | `/search/entities` | `router/routes_search.py:10` `search_entities()` |
| GET | `/search/{entity}` | `router/routes_search.py:20` `search_entity(entity, request)` |
| GET | `/search/{entity}/suggest` | `router/routes_search.py:62` `search_suggest(entity, field, q)` |
| GET | `/browse/prefix` | `router/routes_search.py:88` `browse_prefix(prefix, limit)` |
| GET | `/` | `router/routes_static.py:24` `root()` |

---

## Part 3 — Every function, file by file


### [A] `cache/_cache_stats.py` — 69 lines, 4 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 3 | `_CacheStats.size(self)` | 1 | 13 | 1 | Return number of currently stored entries. |
| 17 | `_CacheStats.stats(self)` | 1 | 22 | 4 | Return cache statistics. |
| 40 | `_CacheStats.keys(self)` | 1 | 20 | 9 | Return keys from most recently used to least recently used. |
| 61 | `_CacheStats.__repr__(self)` | 1 | 9 |  | return ( |

### [A] `cache/_linked_list_ops.py` — 72 lines, 4 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 8 | `_LinkedListOps._remove_node(self, node)` | 2 | 18 | **0** | Remove a node from the doubly linked list. |
| 27 | `_LinkedListOps._add_to_front(self, node)` | 2 | 18 | **0** | Insert node immediately after HEAD. |
| 46 | `_LinkedListOps._move_to_front(self, node)` | 2 | 9 | **0** | Move an existing node to the front. |
| 56 | `_LinkedListOps._remove_lru(self)` | 1 | 17 | **0** | Remove and return the least recently used node. |

### [A] `cache/_load_state.py` — 10 lines, 1 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 8 | `_LoadState.__init__(self)` | 1 | 3 |  | self.done = threading.Event() self.error: Optional[BaseException] = None |

### [B] `cache/hot_keys.py` — 138 lines, 6 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 40 | `HotKeyManager.__init__(self, max_keys)` | 2 | 14 |  | if max_keys <= 0: self.max_keys = max_keys |
| 59 | `HotKeyManager.record(self, key)` | 2 | 13 | 4 | One access to `key` seen by the router. |
| 73 | `HotKeyManager.merge(self, node_counts)` | 2 | 41 | 2 | Fold a node's Layer 6.2 key ranking into the cluster view. |
| 119 | `HotKeyManager.top_keys(self, limit)` | 2 | 5 | 1 | The `limit` most accessed keys across the cluster. |
| 125 | `HotKeyManager.stats(self)` | 1 | 8 | 4 | with self.lock: |
| 134 | `HotKeyManager.clear(self)` | 1 | 5 | 6 | with self.lock: |

### [A] `cache/lru_cache.py` — 374 lines, 9 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 32 | `LRUCache.__init__(self, capacity, on_eviction)` | 3 | 41 |  | if capacity <= 0: self.capacity = capacity |
| 78 | `LRUCache.get(self, key)` | 2 | 40 | 20 | Get a value from the cache. |
| 119 | `LRUCache.get_or_set(self, key, loader, ttl)` | 4 | 47 | **0** | Return a cached value, loading it once when it is missing. |
| 167 | `LRUCache.get_entry(self, key)` | 2 | 27 | **0** | Layer 7.3 - versioned read. |
| 195 | `LRUCache.set(self, key, value, ttl, sequence)` | 5 | 88 | 51 | Insert or update a cache entry. |
| 284 | `LRUCache.delete(self, key)` | 2 | 23 | 16 | Delete an entry. |
| 308 | `LRUCache.delete_prefix(self, prefix)` | 2 | 27 | 3 | Delete every key that starts with `prefix`. |
| 336 | `LRUCache.exists(self, key)` | 2 | 25 | 6 | Check whether a valid entry exists. |
| 362 | `LRUCache.clear(self)` | 1 | 13 | 6 | Remove every cache entry. |

### [A] `cache/node.py` — 57 lines, 3 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 19 | `Node.__init__(self, key, value, expires_at, sequence)` | 5 | 17 |  | self.key = key self.value = value |
| 37 | `Node.is_expired(self, current_time)` | 2 | 11 | **0** | Returns True if this cache entry has expired. |
| 49 | `Node.__repr__(self)` | 1 | 9 |  | return ( |

### [B] `cache/statistics.py` — 278 lines, 13 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 42 | `CacheStatistics.__init__(self, max_tracked_keys, window_seconds, auto_rotate)` | 4 | 43 |  | if max_tracked_keys <= 0: if window_seconds <= 0: |
| 90 | `CacheStatistics.record_hit(self, key)` | 2 | 6 | 1 | One cache HIT for `key`. |
| 97 | `CacheStatistics.record_miss(self, key)` | 2 | 6 | 1 | One cache MISS for `key` (still an access - it was requested). |
| 104 | `CacheStatistics.record_set(self)` | 1 | 3 | 2 | with self.lock: |
| 108 | `CacheStatistics.record_delete(self)` | 1 | 3 | 2 | with self.lock: |
| 112 | `CacheStatistics.record_eviction(self, key)` | 2 | 11 | 1 | One capacity eviction. |
| 124 | `CacheStatistics._note_access(self, key)` | 2 | 15 | **0** | Bump one key's counter, respecting the tracking bound. |
| 144 | `CacheStatistics.snapshot(self, include_keys)` | 2 | 40 | 1 | Current-window counters. |
| 185 | `CacheStatistics.top_keys(self, limit)` | 2 | 5 | 1 | The `limit` most accessed keys of the current window. |
| 195 | `CacheStatistics.rotate(self, top_keys_limit)` | 2 | 46 | **0** | Close the current window and start a fresh one. |
| 246 | `CacheStatistics.start(self)` | 1 | 17 | 4 | Start the background window-rotation thread (idempotent). |
| 264 | `CacheStatistics.stop(self)` | 1 | 10 | 4 | Stop the rotation thread and wait for it to exit. |
| 275 | `CacheStatistics._rotation_loop(self)` | 1 | 4 | **0** | while not self._stop_event.wait(self.window_seconds): |

### [C] `cluster/consistent_hash.py` — 195 lines, 10 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 7 | `ConsistentHashRing.__init__(self, virtual_nodes)` | 2 | 11 |  | self.virtual_nodes = virtual_nodes self.ring = {} |
| 23 | `ConsistentHashRing._hash(self, key)` | 2 | 7 | **0** | digest = hashlib.md5( return int(digest, 16) |
| 35 | `ConsistentHashRing.add_node(self, node)` | 2 | 22 | **0** | if node in self.node_positions: self.node_positions[node] = [] |
| 62 | `ConsistentHashRing.remove_node(self, node)` | 2 | 23 | **0** | if node not in self.node_positions: positions = self.node_positions[node] |
| 90 | `ConsistentHashRing.get_node(self, key)` | 2 | 8 | 2 | nodes = self.get_nodes(key, 1) if not nodes: |
| 103 | `ConsistentHashRing.get_nodes(self, key, count)` | 3 | 46 | 2 | Return up to `count` distinct physical nodes |
| 154 | `ConsistentHashRing.copy(self)` | 1 | 15 | **0** | Independent shallow copy of this ring. |
| 170 | `ConsistentHashRing.with_node(self, node)` | 2 | 14 | **0** | Ring copy that also contains `node`. |
| 189 | `ConsistentHashRing.get_all_nodes(self)` | 1 | 4 | **0** | Return all physical nodes currently in the ring. |
| 194 | `ConsistentHashRing.__len__(self)` | 1 | 2 |  | return len(self.node_positions) |

### [C] `cluster/events.py` — 51 lines, 5 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 16 | `LifecycleEvent.__init__(self, node, old_state, new_state)` | 4 | 5 |  | self.node = node self.old_state = old_state |
| 23 | `LifecycleEvent.old(self)` | 1 | 2 | **0** | return self.old_state.value if self.old_state is not None else "none" |
| 27 | `LifecycleEvent.new(self)` | 1 | 2 | **0** | return self.new_state.value if self.new_state is not None else "none" |
| 30 | `LifecycleEvent.__repr__(self)` | 1 | 2 |  | return f"LifecycleEvent({self.node}: {self.old} -> {self.new})" |
| 34 | `fire_listeners(manager, event)` | 2 | 18 | 5 | Deliver `event` to every registered listener. |

### [C] `cluster/health_monitor.py` — 64 lines, 3 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 5 | `check_node(node)` | 1 | 20 | 8 | Hit the /health endpoint of a single node. |
| 27 | `run_health_pass(manager, checker)` | 2 | 25 | 3 | Check every known node exactly once and feed the results |
| 54 | `health_check_loop(manager, checker)` | 2 | 11 | **0** | Infinite loop that checks every node periodically. |

### [C] `cluster/node_activation.py` — 61 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 6 | `activate_node(manager, node)` | 2 | 29 | **0** | Legacy Layer 4 helper: put a node straight into service. |
| 37 | `deactivate_node(manager, node)` | 2 | 25 | **0** | Legacy Layer 4 helper: take a node out of service (FAILED). |

### [C] `cluster/node_lifecycle.py` — 237 lines, 5 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 19 | `enter_state(manager, node, target, force)` | 4 | 49 | 6 | Move a node to `target`, applying the side effects: |
| 74 | `register(manager, node, initial_state)` | 3 | 26 | 11 | Add a node to the manager in a given lifecycle state. |
| 106 | `_recovery_gate_satisfied(manager, node)` | 2 | 21 | 1 | Ask the optional recovery gate (Layer 6.3 warm-up) whether this |
| 129 | `_transition(manager, node, target)` | 3 | 16 | 6 | enter_state + build the lifecycle event for it. |
| 147 | `on_health_result(manager, node, healthy)` | 3 | 91 | 8 | Feed one health-check outcome into the lifecycle. |

### [C] `cluster/node_manager.py` — 448 lines, 21 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 33 | `NodeManager.__init__(self, nodes, virtual_nodes, replication_factor, health_interval, failure_threshold, initial_state, recovery_threshold)` | 8 | 59 |  | nodes = list(nodes) self.replication_factor = replication_factor |
| 97 | `NodeManager.get_nodes(self, key)` | 2 | 14 | 2 | Return up to replication_factor distinct physical |
| 112 | `NodeManager.get_primary(self, key)` | 2 | 9 | **0** | Return only the primary node for a key. |
| 122 | `NodeManager.ring_view(self, node)` | 2 | 17 | 2 | Snapshot of the ring, optionally including `node` even when |
| 140 | `NodeManager.declared_ring_view(self)` | 1 | 29 | **0** | Snapshot of the ring over EVERY declared node, gated or not. |
| 170 | `NodeManager.expected_primary(self, key, node)` | 3 | 14 | **0** | Who WOULD be the primary for `key` once `node` rejoins |
| 189 | `NodeManager.add_listener(self, callback)` | 2 | 9 | **0** | Register for lifecycle events. Callbacks receive a |
| 199 | `NodeManager.remove_listener(self, callback)` | 2 | 6 | **0** | with self.lock: |
| 206 | `NodeManager.add_health_hook(self, callback)` | 2 | 8 | **0** | Register callback(manager): runs after every health pass, |
| 215 | `NodeManager.set_recovery_gate(self, gate)` | 2 | 12 | **0** | Set the extra RECOVERING -> READY condition. |
| 232 | `NodeManager.start_node(self, node)` | 2 | 37 | 1 | Bring a node online (gated). |
| 270 | `NodeManager.mark_recovering(self, node)` | 2 | 9 | **0** | Gate a node: RECOVERING, not in the hash ring. |
| 280 | `NodeManager.mark_ready(self, node)` | 2 | 10 | 1 | Put a node into service: READY, in the hash ring. |
| 291 | `NodeManager.mark_failed(self, node)` | 2 | 8 | **0** | Take a node out of service: FAILED, evicted from the ring. |
| 300 | `NodeManager.is_available(self, node)` | 2 | 11 | **0** | True if the node currently receives traffic. |
| 312 | `NodeManager._mark(self, node, target)` | 3 | 25 | **0** | Validated transition helper for the named lifecycle ops. |
| 338 | `NodeManager.register_node(self, node, initial_state)` | 3 | 12 | 1 | Add a node at runtime. |
| 351 | `NodeManager.deregister_node(self, node)` | 2 | 29 | 1 | Remove a node from the cluster entirely (Layer 9 scale-down |
| 381 | `NodeManager.get_node_state(self, node)` | 2 | 8 | **0** | Return the lifecycle state of a single node. |
| 394 | `NodeManager.start_health_monitor(self)` | 1 | 12 | **0** | Start the background health-check thread. |
| 411 | `NodeManager.get_status(self)` | 1 | 38 | **0** | Return a snapshot of the cluster state. |

### [C] `cluster/node_state.py` — 77 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 62 | `InvalidTransitionError.__init__(self, node, current, target)` | 4 | 10 |  | self.node = node self.current = current |
| 74 | `can_transition(current, target)` | 2 | 4 | 1 | True if `current -> target` is a legal transition. |

### [D] `database/_connection_mixin.py` — 53 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 9 | `_ConnectionMixin._get_connection(self)` | 1 | 11 | **0** | connection = sqlite3.connect( connection.row_factory = sqlite3.Row |
| 21 | `_ConnectionMixin._initialize_database(self)` | 1 | 33 | **0** | connection = self._get_connection() try: |

### [D] `database/_crud_mixin.py` — 372 lines, 12 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 27 | `_classify(error)` | 1 | 16 | 1 | Turn a raw sqlite3 error into the right domain exception. |
| 49 | `_CrudMixin.query(self, sql, params, max_rows)` | 4 | 45 | 6 | Run one read-only statement and return the result as |
| 95 | `_CrudMixin.get(self, key)` | 2 | 28 | 19 | connection = self._get_connection() try: |
| 124 | `_CrudMixin.set(self, key, value)` | 3 | 27 | 50 | connection = self._get_connection() try: |
| 152 | `_CrudMixin.set_many(self, items)` | 2 | 40 | 1 | Batch upsert. |
| 193 | `_CrudMixin.delete(self, key)` | 2 | 24 | 15 | connection = self._get_connection() try: |
| 218 | `_CrudMixin.delete_prefix(self, prefix)` | 2 | 37 | 2 | Delete every key that starts with `prefix`; return the |
| 256 | `_CrudMixin.exists(self, key)` | 2 | 24 | 5 | connection = self._get_connection() try: |
| 281 | `_CrudMixin.count(self)` | 1 | 16 | 4 | connection = self._get_connection() try: |
| 298 | `_CrudMixin.keys(self, limit)` | 2 | 24 | 9 | connection = self._get_connection() try: |
| 323 | `_CrudMixin.iter_keys(self, batch_size)` | 2 | 33 | 2 | Layer 7.3 - stream every key without loading them all. |
| 357 | `_CrudMixin.clear(self)` | 1 | 16 | 6 | connection = self._get_connection() try: |

### [D] `database/multi_repository.py` — 257 lines, 18 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 30 | `MultiDatasetRepository.__init__(self, db_path, timeout, registry)` | 4 | 16 |  | self.db_path = db_path self.timeout = timeout |
| 49 | `MultiDatasetRepository._sync(self)` | 1 | 24 | **0** | Create/drop per-dataset repositories after manifest changes. |
| 74 | `MultiDatasetRepository._repo_for_key(self, key)` | 2 | 14 | **0** | self._sync() best = None |
| 91 | `MultiDatasetRepository.repo_for(self, prefix)` | 2 | 16 | 3 | Repository owning every key under `prefix` (search SQL). |
| 108 | `MultiDatasetRepository.repos_matching(self, prefix)` | 2 | 22 | 1 | Repositories that can hold keys matching `prefix`. |
| 131 | `MultiDatasetRepository.repo_named(self, name)` | 2 | 13 | 2 | Repository for one dataset name, or the system store. |
| 145 | `MultiDatasetRepository.repos(self)` | 1 | 9 | **0** | (label, repository) pairs: system first, then by name. |
| 157 | `MultiDatasetRepository.get(self, key)` | 2 | 2 | 20 | return self._repo_for_key(key).get(key) |
| 160 | `MultiDatasetRepository.set(self, key, value)` | 3 | 2 | 51 | self._repo_for_key(key).set(key, value) |
| 163 | `MultiDatasetRepository.delete(self, key)` | 2 | 2 | 16 | return self._repo_for_key(key).delete(key) |
| 166 | `MultiDatasetRepository.delete_prefix(self, prefix)` | 2 | 3 | 3 | Delete every key under `prefix` in the store that owns it. |
| 170 | `MultiDatasetRepository.exists(self, key)` | 2 | 2 | 6 | return self._repo_for_key(key).exists(key) |
| 173 | `MultiDatasetRepository.set_many(self, items)` | 2 | 13 | 1 | Batch upsert grouped by owning repository. |
| 187 | `MultiDatasetRepository.count(self)` | 1 | 7 | 4 | self._sync() return self.system.count() + sum( |
| 195 | `MultiDatasetRepository.keys(self, limit)` | 2 | 14 | 9 | self._sync() streams = [self.system.keys(limit)] + [ |
| 210 | `MultiDatasetRepository.iter_keys(self, batch_size)` | 2 | 9 | 2 | self._sync() streams = [self.system.iter_keys(batch_size)] + [ |
| 220 | `MultiDatasetRepository.clear(self)` | 1 | 8 | 6 | self._sync() self.system.clear() |
| 229 | `MultiDatasetRepository.query(self, sql, params, max_rows)` | 4 | 29 | 6 | Read-only SQL with best-effort repository inference. |

### [D] `database/registry.py` — 569 lines, 22 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 51 | `slugify(text)` | 1 | 19 | 12 | File stem -> dataset name: lowercase, underscores, no spaces. |
| 72 | `safe_key(value)` | 1 | 14 | 5 | URL-path-safe fragment of a raw key value. |
| 88 | `coerce(value)` | 1 | 49 | 17 | Normalize one record value into a JSON-safe Python value. |
| 143 | `detect_schema(sample_rows, max_distinct)` | 2 | 66 | 5 | Per-column type + low-cardinality values from a sample. |
| 211 | `_normalize_column(column)` | 1 | 2 | 2 | return re.sub(r"[^a-z0-9]", "", str(column).lower()) |
| 215 | `detect_key_column(columns, sample_rows, requested)` | 3 | 59 | 11 | Pick the column that builds cache keys - or None for row index. |
| 290 | `DatasetRegistry.__init__(self, db_path, drop_dir)` | 3 | 14 |  | resolved = db_path or os.environ.get( self.base_dir = os.path.dirname(os.path.abspath(resolved)) |
| 307 | `DatasetRegistry._stat(self)` | 1 | 6 | **0** | try: return (info.st_mtime_ns, info.st_size) |
| 314 | `DatasetRegistry._load(self)` | 1 | 14 | **0** | try: if not isinstance(payload, dict): |
| 329 | `DatasetRegistry.refresh(self)` | 1 | 11 | 4 | Re-read when the file changed; return the revision. |
| 341 | `DatasetRegistry.revision(self)` | 1 | 2 | 1 | return self.refresh() |
| 344 | `DatasetRegistry.entries(self)` | 1 | 3 | 4 | self.refresh() return dict(self._entries) |
| 348 | `DatasetRegistry.get(self, name)` | 2 | 2 | 20 | return self.entries().get(name) |
| 351 | `DatasetRegistry.names(self)` | 1 | 2 | **0** | return sorted(self.entries()) |
| 356 | `DatasetRegistry.dataset_db_path(self, name)` | 2 | 2 | **0** | return os.path.join(self.datasets_dir, f"{name}.db") |
| 359 | `DatasetRegistry.register(self, name, prefix, db_path, source_file, file_format, key_column, row_count, schema, source_size, source_mtime)` | 11 | 55 | 11 | Add or replace one manifest entry and persist it. |
| 415 | `DatasetRegistry.unregister(self, name, remove_file)` | 3 | 19 | **0** | Drop one entry; by default also delete its SQLite file. |
| 435 | `DatasetRegistry.update(self, name, **fields)` | 3 | 21 | **0** | Partial update of one manifest entry (Layer 9 dataset admin). |
| 457 | `DatasetRegistry._save(self, datasets)` | 2 | 20 | **0** | Path(self.base_dir).mkdir(parents=True, exist_ok=True) payload = { |
| 480 | `DatasetRegistry.prefix_for(self, key)` | 2 | 13 | **0** | Longest registered prefix that owns `key` ("" = system). |
| 494 | `DatasetRegistry.entry_for(self, key)` | 2 | 15 | **0** | The manifest entry whose prefix owns `key` (or None). |
| 512 | `DatasetRegistry.scan_drop_folder(self, drop_dir)` | 2 | 58 | **0** | Supported files in the drop folder that need importing. |

### [D] `database/repository.py` — 41 lines, 1 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 33 | `DatabaseRepository.__init__(self, db_path, timeout)` | 3 | 9 |  | self.db_path = db_path self.timeout = timeout |

### [U] `import_dataset.py` — 685 lines, 17 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 76 | `build_parser()` | 0 | 29 | 2 | parser = argparse.ArgumentParser( parser.add_argument("--file", default=None, help="Path to the dataset file") |
| 107 | `parse_args(argv)` | 1 | 2 | 2 | return build_parser().parse_args(argv) |
| 115 | `detect_format(path)` | 1 | 14 | 16 | File name -> format name (raises ValueError when unknown). |
| 131 | `_read_delimited(handle, delimiter)` | 2 | 2 | 2 | yield from csv.DictReader(handle, delimiter=delimiter) |
| 135 | `_read_json(handle)` | 1 | 13 | 2 | data = json.load(handle) if isinstance(data, dict): |
| 150 | `_read_jsonl(handle)` | 1 | 12 | 2 | for line_number, line in enumerate(handle, start=1): |
| 164 | `_read_pandas(source, file_format)` | 2 | 37 | 2 | Parquet / Excel records via pandas (lazy import). |
| 203 | `_zip_member(path)` | 1 | 19 | 1 | First supported data file inside a zip archive. |
| 224 | `read_rows(path, file_format)` | 2 | 49 | 10 | Yield one dict per record, for every supported format. |
| 279 | `entry_from_record(record, key_column, prefix, row_number)` | 4 | 48 | 7 | One dataset record -> one (key, json-safe value) pair. |
| 333 | `import_file(file, name, prefix, key_column, limit, dry_run, reimport, registry, verbose)` | 9 | 166 | 26 | Import one file into its own SQLite file + registry entry. |
| 501 | `_discard(path)` | 1 | 5 | 1 | try: |
| 508 | `seed_drop_folder(drop_dir, registry, limit, verbose)` | 4 | 51 | 7 | Import every unregistered (or changed) file in the drop folder. |
| 565 | `_print_plan(summary)` | 1 | 11 | 1 | key_note = "" if summary["key_column"] else " (row number)" print(f"File          : {summary['file']}") |
| 578 | `_print_result(summary)` | 1 | 13 | 1 | print(f"Records read  : {summary['rows']}") if summary["duplicates"]: |
| 593 | `list_datasets(registry)` | 1 | 24 | 4 | One info dict per registered dataset (rows read live). |
| 623 | `main(argv)` | 1 | 59 | 10 | parser = build_parser() args = parser.parse_args(argv) |

### [T] `main.py` — 403 lines, 14 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 23 | `parse_args()` | 0 | 5 | 2 | parser = argparse.ArgumentParser(description="Layer 2 - Cache Node") parser.add_argument("--port", type=int, default=... |
| 74 | `health()` | 0 | 5 | ★ | return { |
| 86 | `get_value(key)` | 1 | 19 | ★ | value = cache.get(key) if value is None: |
| 112 | `set_value(key, entry)` | 2 | 26 | ★ | if entry.ttl is None: statistics.record_set() |
| 145 | `delete_value(key)` | 1 | 17 | ★ | statistics.record_delete() deleted = cache.delete(key) |
| 186 | `internal_replicate(key, entry)` | 2 | 17 | ★ | Apply one replicated write locally (no fan-out). |
| 206 | `internal_delete(key)` | 1 | 13 | ★ | Apply one replicated delete locally (no fan-out). |
| 222 | `internal_entry(key)` | 1 | 20 | ★ | Versioned read: value + replication sequence. |
| 254 | `internal_flush_prefix(prefix)` | 1 | 12 | ★ | removed = cache.delete_prefix(prefix) for _ in range(removed): |
| 277 | `internal_shutdown()` | 0 | 13 | ★ | Ask this process's server to exit gracefully. |
| 297 | `exists(key)` | 1 | 6 | ★ | return { |
| 310 | `stats()` | 0 | 3 | ★ | return cache.stats() |
| 320 | `stats_window(limit)` | 1 | 13 | ★ | Current statistics window: operation counters, hit rate and |
| 336 | `stats_top_keys(limit)` | 1 | 10 | ★ | Ranking of the most accessed keys in the current window. |

### [S] `router/__init__.py` — 48 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 28 | `__getattr__(name)` | 1 | 17 | **0** | target = _EXPORTS.get(name) if target is None: |
| 47 | `__dir__()` | 0 | 2 | **0** | return sorted(set(list(globals())) \| set(__all__)) |

### [S] `router/_components.py` — 289 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 114 | `_make_seeder(repo)` | 1 | 13 | 2 | Build the seed pool for `repo`, or None when disabled. |
| 171 | `configure_cluster(nodes, replication_factor, virtual_nodes, health_interval, failure_threshold, initial_state, recovery_threshold, start_monitor)` | 8 | 119 | 1 | Rebuild the cluster components (used by tests / demos). |

### [S] `router/app.py` — 29 lines, 1 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 7 | `lifespan(app)` | 1 | 15 | **0** | Stop the node processes THIS router spawned (Cluster control / |

### [S] `router/config.py` — 131 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 91 | `normalize_node(entry)` | 1 | 29 | 1 | One node entry -> one usable URL. |
| 122 | `nodes_from_env()` | 0 | 10 | 12 | Read the cluster node list from CACHE_NODES (URLs or bare ports). |

### [N] `router/routes_cache.py` — 113 lines, 3 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 11 | `get_cache(key)` | 1 | 26 | ★ | try: if result is None: |
| 40 | `set_cache(key, entry)` | 2 | 46 | ★ | try: warning = None |
| 89 | `delete_cache(key)` | 1 | 25 | ★ | Layer 9 - EVICT ONLY: drop the key from every cache node, keep |

### [O] `router/routes_cluster.py` — 181 lines, 8 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 10 | `cluster_status()` | 0 | 3 | ★ | return _components.node_manager.get_status() |
| 16 | `cluster_hot_keys(limit, per_node)` | 2 | 60 | ★ | Layer 6.3 - merged cluster hot-key ranking. |
| 79 | `cluster_warmup_status()` | 0 | 24 | ★ | Layer 6.3 - warm-up state per node. |
| 106 | `cluster_warmup_trigger(node)` | 1 | 22 | ★ | Manually start (or re-request) a warm-up for one node. |
| 131 | `circuit_breaker_status()` | 0 | 3 | ★ | return _components.cache_service.circuit_breaker_status() |
| 141 | `cluster_replication_status()` | 0 | 7 | ★ | Layer 7.3/7.5 - replication log, per-node ack high-water marks |
| 151 | `cluster_resync(node)` | 1 | 22 | ★ | Layer 7.3 - synchronously bring one node's data current. |
| 176 | `health()` | 0 | 6 | ★ | return { |

### [O] `router/routes_dashboard.py` — 160 lines, 3 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 10 | `_node_snapshot(node)` | 1 | 45 | 1 | Pull one node's lifetime + windowed stats. |
| 58 | `dashboard_aggregate()` | 0 | 94 | ★ | Layer 8 - one call for the whole dashboard. |
| 155 | `dashboard_telemetry_reset()` | 0 | 6 | ★ | Layer 8 - zero the read counters (used by the Cache Lab demo). |

### [R] `router/routes_datasets.py` — 151 lines, 8 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 37 | `_admin()` | 0 | 2 | 7 | return _components.admin |
| 42 | `list_datasets()` | 0 | 7 | ★ | Every registered dataset with prefix, schema and live rows. |
| 52 | `create_dataset(body)` | 1 | 11 | ★ | New isolated dataset: own SQLite file, own prefix, own entity. |
| 66 | `get_dataset(name)` | 1 | 8 | ★ | try: |
| 77 | `delete_dataset(name)` | 1 | 15 | ★ | Remove a dataset everywhere: cached rows flushed from every |
| 95 | `list_entries(name, limit, offset)` | 3 | 13 | ★ | Paged key/value rows of one dataset (sorted by key). |
| 111 | `add_entry(name, body)` | 2 | 19 | ★ | Add one entry - conflict-free: the key is derived server-side |
| 133 | `delete_entry(name, key)` | 2 | 19 | ★ | Delete one entry everywhere: database row removed, every cache |

### [N] `router/routes_db.py` — 75 lines, 2 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 10 | `db_get(key)` | 1 | 25 | ★ | try: if value is None: |
| 38 | `db_delete(key)` | 1 | 38 | ★ | Layer 9 - delete the DATABASE row and cascade the cache |

### [P] `router/routes_manage.py` — 379 lines, 14 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 42 | `_url_for(port)` | 1 | 2 | 3 | return f"http://127.0.0.1:{port}" |
| 46 | `_port_of(url)` | 1 | 5 | 3 | try: |
| 53 | `_pool()` | 0 | 2 | 4 | return [NODE_PORT_BASE + i for i in range(MAX_NODES)] |
| 57 | `_node_stats(url)` | 1 | 11 | 1 | Best-effort /stats snapshot (doubles as a liveness probe). |
| 70 | `_node_entry(url, states, active)` | 3 | 23 | 1 | port = _port_of(url) owned = port is not None and process_manager.is_running(port) |
| 95 | `_wait_healthy(urls, timeout)` | 2 | 24 | 3 | Poll /health of every url until all answer or time runs out. |
| 121 | `_shutdown_node(url)` | 1 | 8 | 1 | Ask a node to exit gracefully through its internal endpoint. |
| 131 | `_start_node(port)` | 1 | 34 | 3 | Make `port` a cluster member: adopt it when something already |
| 167 | `_stop_node(port)` | 1 | 43 | 3 | Take `port` out of the cluster and stop its process. |
| 225 | `cluster_nodes()` | 0 | 23 | ★ | Every known node with lifecycle state, process info (owned / |
| 251 | `cluster_scale(body)` | 1 | 72 | ★ | Scale the cluster to `target` nodes. |
| 326 | `cluster_node_start(port)` | 1 | 21 | ★ | Spawn (or adopt) one node and wait briefly for /health. |
| 350 | `cluster_node_stop(port)` | 1 | 4 | ★ | Deregister from the ring and stop the process on `port`. |
| 357 | `cluster_node_restart(port)` | 1 | 23 | ★ | Stop, then start again - membership re-enters through STARTING. |

### [Q] `router/routes_search.py` — 109 lines, 4 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 10 | `search_entities()` | 0 | 7 | ★ | Layer 8 - what the UI needs to build filter sidebars: per-entity |
| 20 | `search_entity(entity, request)` | 2 | 39 | ★ | Layer 8 - cached search. |
| 62 | `search_suggest(entity, field, q)` | 3 | 23 | ★ | Layer 8 - distinct-value autocomplete for one facet field. |
| 88 | `browse_prefix(prefix, limit)` | 2 | 22 | ★ | Layer 8 - key listing for the inspector. |

### [N] `router/routes_static.py` — 27 lines, 1 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 24 | `root()` | 0 | 4 | ★ | One command to reach the UI: open the router, land on /ui. |

### [M] `services/cache_service.py` — 249 lines, 7 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 38 | `CacheService.__init__(self, cache, database, single_flight, circuit_breaker, hot_keys, replication, telemetry)` | 8 | 31 |  | self.cache = cache self.database = database |
| 74 | `CacheService.get(self, key)` | 2 | 24 | 20 | Return the value or None (cache + database miss). |
| 103 | `CacheService._load_from_database(self, key)` | 2 | 40 | **0** | value, node = self.cache.get(key) if value is not None: |
| 148 | `CacheService.set(self, key, value, ttl)` | 4 | 32 | 51 | self.hot_keys.record(key) sequence = None |
| 185 | `CacheService.delete(self, key)` | 2 | 28 | 16 | self.hot_keys.record(key) sequence = None |
| 218 | `CacheService.evict(self, key)` | 2 | 23 | **0** | Remove `key` from every cache node that holds it - the |
| 246 | `CacheService.circuit_breaker_status(self)` | 1 | 4 | 1 | Expose circuit breaker state and stats for the status endpoint. |

### [E] `services/circuit_breaker.py` — 198 lines, 8 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 38 | `CircuitBreaker.__init__(self, failure_threshold, recovery_timeout, success_threshold)` | 4 | 26 |  | if failure_threshold < 1: if recovery_timeout <= 0: |
| 69 | `CircuitBreaker.call(self, func, *args, **kwargs)` | 4 | 30 | **0** | Run `func(*args, **kwargs)` through the breaker. |
| 104 | `CircuitBreaker._should_reject(self)` | 1 | 21 | **0** | with self._lock: |
| 126 | `CircuitBreaker._record_success(self)` | 1 | 19 | **0** | with self._lock: |
| 146 | `CircuitBreaker._record_failure(self)` | 1 | 20 | **0** | with self._lock: |
| 171 | `CircuitBreaker.get_state(self)` | 1 | 5 | **0** | Current state as 'closed' \| 'open' \| 'half_open'. |
| 177 | `CircuitBreaker.get_stats(self)` | 1 | 13 | **0** | with self._lock: |
| 191 | `CircuitBreaker.reset(self)` | 1 | 8 | 1 | with self._lock: |

### [L] `services/dataset_admin.py` — 389 lines, 16 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 46 | `DatasetAdmin.__init__(self, registry, database, cache_service, distributed_cache, replication)` | 6 | 8 |  | self.registry = registry self.database = database |
| 59 | `DatasetAdmin.list(self)` | 1 | 14 | 50 | Every registered dataset with its live row count. |
| 74 | `DatasetAdmin.get(self, name)` | 2 | 11 | 20 | One dataset (raises KeyError when unknown). |
| 86 | `DatasetAdmin.entries(self, name, limit, offset)` | 4 | 21 | 4 | Paged key/value rows of one dataset (sorted by key). |
| 112 | `DatasetAdmin.create(self, name, key_column)` | 3 | 24 | 2 | Register a brand-new dataset: its own data/datasets/<name>.db, |
| 137 | `DatasetAdmin.delete_dataset(self, name)` | 2 | 24 | 2 | Remove a dataset everywhere: nodes, log, search caches, |
| 166 | `DatasetAdmin.add_entry(self, name, value)` | 3 | 33 | 2 | Add (or upsert, when the id already exists) one entry. |
| 200 | `DatasetAdmin.delete_entry(self, name, key)` | 3 | 36 | 2 | Delete one entry everywhere: the row is removed (DB), every |
| 241 | `DatasetAdmin.after_key_change(self, key)` | 2 | 20 | **0** | Called after a raw /cache PUT or /db DELETE touched a row |
| 266 | `DatasetAdmin.purge_search_caches(self, name)` | 2 | 27 | **0** | Drop every cached search/suggest result for one entity: |
| 298 | `DatasetAdmin._entry(self, name)` | 2 | 9 | 2 | Manifest entry for a (possibly un-slugified) dataset name. |
| 308 | `DatasetAdmin._repo(self, name, entry)` | 3 | 2 | 1 | return self.database.repo_named(name) or self.database.system |
| 311 | `DatasetAdmin._key_for(self, entry, value, repo)` | 4 | 21 | **0** | Conflict-free key derivation (see module docstring). |
| 334 | `DatasetAdmin._next_sequence(repo, prefix)` | 2 | 16 | **0** | One past the highest numeric suffix already in use. |
| 351 | `DatasetAdmin._sample_rows(self, repo, limit)` | 3 | 13 | **0** | First N dict-shaped rows, for schema detection. |
| 365 | `DatasetAdmin._sync_stats(self, name)` | 2 | 25 | **0** | Re-count rows and re-detect the schema after a mutation, so |

### [M] `services/distributed_cache.py` — 241 lines, 7 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 18 | `DistributedCache.__init__(self, node_manager, timeout)` | 3 | 7 |  | self.node_manager = node_manager self.timeout = timeout |
| 30 | `DistributedCache.get(self, key)` | 2 | 25 | 20 | for node in self.node_manager.get_nodes(key): return None, None |
| 60 | `DistributedCache.set(self, key, value, ttl, sequence)` | 5 | 46 | 51 | Fan one write out to the primary + replicas. |
| 111 | `DistributedCache.delete(self, key, sequence)` | 3 | 23 | 16 | deleted_from = [] for node in self.node_manager.get_nodes(key): |
| 139 | `DistributedCache.flush_prefix(self, prefix)` | 2 | 35 | **0** | Drop every key under `prefix` on every KNOWN node. |
| 179 | `DistributedCache.write_to_node(self, node, key, value, ttl, sequence)` | 6 | 38 | 3 | Write `key` to exactly `node`, bypassing ring placement. |
| 222 | `DistributedCache.delete_from_node(self, node, key)` | 3 | 20 | 1 | Delete `key` from exactly `node` (no ring placement). |

### [K] `services/process_manager.py` — 292 lines, 13 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 33 | `project_python()` | 0 | 13 | 5 | Prefer the project venv so every process runs the right deps. |
| 48 | `_default_spawner(command, cwd, log_path)` | 3 | 18 | **0** | subprocess.Popen with output captured into `log_path`. |
| 68 | `_kill_pid(pid)` | 1 | 23 | 1 | Best-effort kill of a PID we do not own (hung node fallback). |
| 96 | `ProcessManager.__init__(self, root, spawner, log_dir, pid_dir)` | 5 | 11 |  | self.root = root or ROOT self._spawner = spawner or _default_spawner |
| 112 | `ProcessManager.command(self, port, capacity)` | 3 | 7 | **0** | return [ |
| 120 | `ProcessManager._pid_path(self, port)` | 2 | 2 | **0** | return os.path.join(self.pid_dir, f"node-{port}.pid") |
| 123 | `ProcessManager._read_pid(self, port)` | 2 | 6 | **0** | try: |
| 134 | `ProcessManager.start(self, port, capacity)` | 3 | 45 | 4 | Start main.py on `port` unless one of our children already |
| 184 | `ProcessManager.stop(self, port, grace, force)` | 4 | 47 | 4 | Stop the node process on `port`. |
| 232 | `ProcessManager.stop_all(self)` | 1 | 13 | 4 | Stop every child of this process (router shutdown hook). |
| 250 | `ProcessManager.is_running(self, port)` | 2 | 7 | 1 | True while one of OUR children on `port` is alive. |
| 258 | `ProcessManager.pid(self, port)` | 2 | 10 | 1 | Our child's PID, else the one from the PID file (may be stale). |
| 269 | `ProcessManager.status(self)` | 1 | 20 | 5 | Snapshot: one entry per port we have a record of. |

### [H] `services/replication.py` — 753 lines, 28 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 38 | `ReplicationManager.__init__(self, node_manager, distributed_cache, database, log_size, snapshot_threshold, enabled)` | 7 | 43 |  | self.node_manager = node_manager self.cache = distributed_cache |
| 86 | `ReplicationManager._replicable_store(self)` | 1 | 21 | **0** | The store whose keys belong in cache nodes. |
| 112 | `ReplicationManager.next_sequence(self)` | 1 | 8 | 2 | Allocate the next cluster-wide write sequence. |
| 121 | `ReplicationManager.current_sequence(self)` | 1 | 5 | 1 | The most recent sequence handed out (0 = no writes yet). |
| 127 | `ReplicationManager.record(self, key, op, value, ttl, sequence)` | 6 | 19 | 4 | Append one mutation to the bounded replication log. |
| 147 | `ReplicationManager.ack(self, node, sequence)` | 3 | 7 | 1 | Mark `node` as having applied through `sequence`. |
| 155 | `ReplicationManager.acked_sequence(self, node)` | 2 | 5 | **0** | Highest sequence `node` applied (0 = never acked). |
| 161 | `ReplicationManager.forget_node(self, node)` | 2 | 9 | **0** | Drop ack + status for a node (explicit restart). |
| 171 | `ReplicationManager.drop_prefix(self, prefix)` | 2 | 21 | **0** | Remove every logged op whose key starts with `prefix`. |
| 193 | `ReplicationManager.entries_since(self, after_sequence)` | 2 | 14 | **0** | Log entries with sequence > `after_sequence`, oldest first. |
| 208 | `ReplicationManager.log_oldest_sequence(self)` | 1 | 5 | **0** | Oldest sequence still in the log (None = empty log). |
| 218 | `ReplicationManager.install(self)` | 1 | 23 | 1 | Wire the replication sync into the node manager (idempotent): |
| 242 | `ReplicationManager._on_lifecycle(self, event)` | 2 | 20 | 1 | Lifecycle listener (post-lock, so HTTP/DB is safe here). |
| 263 | `ReplicationManager._retry_recovering(self, manager)` | 2 | 20 | 1 | Health-pass hook: retry syncs that failed while gated. |
| 284 | `ReplicationManager.sync_ready(self, node)` | 2 | 16 | **0** | The Layer 7 half of the recovery gate: True when `node` has |
| 305 | `ReplicationManager.ensure(self, node)` | 2 | 48 | 4 | Start a sync for `node` unless one is already pending, |
| 354 | `ReplicationManager._run(self, node)` | 2 | 37 | 3 | Thread body: running -> sync_node -> done/failed. |
| 392 | `ReplicationManager.sync_node(self, node)` | 2 | 62 | 1 | Synchronously bring ONE node's data current (Layer 7.3). |
| 455 | `ReplicationManager._needs_snapshot(self, node, acked, current)` | 4 | 37 | **0** | Snapshot when we cannot cheaply prove the node is only a |
| 493 | `ReplicationManager._replay_into(self, node, after_sequence)` | 3 | 30 | **0** | Incremental sync: push every logged mutation the node has |
| 524 | `ReplicationManager._snapshot_node(self, node)` | 2 | 28 | **0** | Snapshot sync: pull every key `node` owns (per the ring |
| 557 | `ReplicationManager.repair_active_nodes(self, limit)` | 2 | 73 | **0** | Layer 7.6 - restore full redundancy after a node left the ring. |
| 631 | `ReplicationManager.repair(self)` | 1 | 18 | **0** | Fire-and-forget repair on a daemon thread (called from the |
| 654 | `ReplicationManager.status(self)` | 1 | 49 | 5 | Snapshot of replication state for the status endpoint. |
| 704 | `ReplicationManager.record_failover(self, failed_node)` | 2 | 14 | **0** | Layer 7.5 - note that a failover just happened. |
| 719 | `ReplicationManager.last_failover(self)` | 1 | 7 | **0** | with self._lock: |
| 727 | `ReplicationManager.failovers(self)` | 1 | 5 | **0** | Bounded failover history, newest last (Layer 7.5). |
| 737 | `ReplicationManager._entry(self, state, attempts, **extra)` | 4 | 17 | 2 | entry = { entry.update(extra) |

### [I] `services/search.py` — 915 lines, 25 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 29 | `_safe(value)` | 1 | 14 | 4 | Canonical, URL-path-safe fragment of a user-supplied value. |
| 45 | `prettify(name)` | 1 | 6 | 9 | Dataset name -> display label ("netflix_titles" -> "Netflix Titles"). |
| 57 | `_param_name(column, used)` | 2 | 17 | 3 | base = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_") or "field" if base in _RESERVED_PARAMS: |
| 76 | `_range_params(column, used)` | 2 | 18 | 1 | base = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_") or "field" if base in _RESERVED_PARAMS: |
| 96 | `_default_sort(sorts, schema)` | 2 | 19 | 1 | (sort key, direction) for a freshly built entity. |
| 117 | `build_entities(entries)` | 1 | 122 | 5 | Registry entries -> search specs generated from their schemas. |
| 276 | `SearchService.__init__(self, cache_service, distributed_cache, single_flight, circuit_breaker, database, telemetry, ttl, page_size_default, page_size_max, entities, registry)` | 12 | 30 |  | self.cache_service = cache_service self.distributed_cache = distributed_cache |
| 311 | `SearchService.entities(self)` | 1 | 54 | 2 | What the UI needs to build its filter sidebars. |
| 366 | `SearchService.search(self, entity, params)` | 3 | 69 | **0** | Run (or serve from cache) one search. |
| 436 | `SearchService.suggest(self, entity, field, q)` | 4 | 76 | **0** | Distinct-value autocomplete for one facet field. Cached like |
| 513 | `SearchService.browse(self, prefix, limit)` | 3 | 51 | **0** | Key listing for the inspector. Deliberately NOT cached: an |
| 569 | `SearchService._run(self, spec, normal)` | 3 | 62 | 3 | Execute the count + page queries and build the envelope. |
| 632 | `SearchService._where(self, spec, normal)` | 3 | 69 | **0** | Build the WHERE clause + positional args (never identifiers). |
| 702 | `SearchService._order(self, spec, normal)` | 3 | 10 | **0** | ORDER BY from the per-entity whitelist (never user input). |
| 717 | `SearchService._spec_map(self)` | 1 | 19 | **0** | Effective entity specs: explicit dict, else the registry. |
| 737 | `SearchService._repo(self, prefix)` | 2 | 6 | 1 | Repository that physically owns `prefix` keys. |
| 744 | `SearchService._repos(self, prefix)` | 2 | 6 | **0** | Repositories that can hold keys matching `prefix`. |
| 751 | `SearchService._spec(self, entity)` | 2 | 17 | **0** | specs = self._spec_map() spec = specs.get(entity) |
| 769 | `SearchService._normalize(self, spec, params)` | 3 | 69 | 2 | Coerce, clamp and canonicalize every accepted parameter. |
| 839 | `SearchService._cache_key(self, entity, normal)` | 3 | 19 | **0** | parts = [f"kw={_safe(normal['q'])}"] for name in sorted(normal["filters"]): |
| 864 | `SearchService._int(value, default, minimum, maximum)` | 4 | 17 | **0** | try: if minimum is not None and number < minimum: |
| 883 | `SearchService._number(value)` | 1 | 12 | **0** | if value in (None, ""): try: |
| 897 | `SearchService._text(value)` | 1 | 6 | **0** | if value in (None, ""): return str(value).strip() or None |
| 905 | `SearchService._truthy(value)` | 1 | 6 | **0** | if isinstance(value, bool): return str(value).strip().lower() in ("1", "true", "yes", "on") |
| 913 | `SearchService._ms(seconds)` | 1 | 3 | **0** | return round(seconds * 1000, 2) |

### [G] `services/seeding.py` — 298 lines, 11 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 50 | `normalize_strategy(value)` | 1 | 6 | 8 | Unknown or blank strategy -> the default ("popular"). |
| 58 | `popularity_order(spec)` | 1 | 22 | 4 | ORDER BY expression for a dataset's "most used" rows, or None. |
| 82 | `dataset_keys(repo, spec, strategy, limit)` | 4 | 34 | 3 | `limit` seed keys from one dataset's store. |
| 118 | `interleave(groups)` | 1 | 20 | 4 | Round-robin merge so every slice stays dataset-diverse. |
| 140 | `seed_keys(database, strategy, per_dataset)` | 3 | 47 | 9 | Candidate seed keys for every registered dataset. |
| 189 | `fetch_capacity(node, timeout)` | 2 | 26 | 2 | The node's real LRU capacity from GET /stats (0 = unknown). |
| 217 | `seed_budget(capacity, fill_ratio)` | 2 | 12 | 8 | How many keys one node may be seeded with. |
| 241 | `CacheSeeder.__init__(self, database, strategy, per_dataset, fill_ratio, fallback_capacity, capacity_fetcher)` | 7 | 20 |  | self.database = database self.strategy = normalize_strategy(strategy) |
| 262 | `CacheSeeder.candidates(self)` | 1 | 8 | 2 | Popularity-ordered keys, interleaved across datasets. |
| 271 | `CacheSeeder.budget(self, node)` | 2 | 18 | 3 | Keys this node may be seeded with (capacity x fill_ratio). |
| 290 | `CacheSeeder.status(self)` | 1 | 9 | 5 | Config snapshot for /cluster/warmup. |

### [E] `services/single_flight.py` — 125 lines, 7 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 13 | `Flight.__init__(self, key)` | 2 | 6 |  | self.key = key self.event = threading.Event() |
| 20 | `Flight.set_result(self, result)` | 2 | 4 | **0** | self.result = result self.event.set() |
| 25 | `Flight.set_error(self, error)` | 2 | 4 | **0** | self.error = error self.event.set() |
| 30 | `Flight.get(self)` | 1 | 9 | 20 | Block until the leader finishes, then return result or re-raise. |
| 60 | `SingleFlight.__init__(self)` | 1 | 7 |  | self._lock = threading.Lock() self._in_flight: Dict[str, Flight] = {} |
| 72 | `SingleFlight.do(self, key, function)` | 3 | 45 | **0** | with self._lock: if not leader: |
| 122 | `SingleFlight.in_flight_keys(self)` | 1 | 4 | **0** | with self._lock: |

### [J] `services/telemetry.py` — 138 lines, 7 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 30 | `RequestTelemetry.__init__(self)` | 1 | 15 |  | self._lock = threading.Lock() self._counters = { |
| 50 | `RequestTelemetry.record_get(self, source)` | 2 | 13 | **0** | Record one point-key read: 'cache', 'database' or 'miss'. |
| 64 | `RequestTelemetry.record_search(self, hit)` | 2 | 9 | **0** | Record one search: cached result set vs a real SQL run. |
| 74 | `RequestTelemetry.record_set(self)` | 1 | 4 | 2 | with self._lock: |
| 79 | `RequestTelemetry.record_delete(self)` | 1 | 4 | 2 | with self._lock: |
| 84 | `RequestTelemetry.reset(self)` | 1 | 9 | 1 | Zero the counters (demo / test helper). |
| 98 | `RequestTelemetry.stats(self)` | 1 | 41 | 4 | Raw counters plus the derived cache-quality ratios. |

### [F] `services/warmup.py` — 556 lines, 16 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 23 | `WarmupRateLimiter.__init__(self, requests_per_second)` | 2 | 18 |  | if requests_per_second is not None and requests_per_second < 0: self.requests_per_second = requests_per_second or 0 |
| 42 | `WarmupRateLimiter.wait(self)` | 1 | 19 | 1 | Block until the next database read is allowed. |
| 104 | `CacheWarmup.__init__(self, node_manager, database, hot_key_manager, distributed_cache, circuit_breaker, enabled, limit, rate_limit, merge_limit, stats_fetcher, seeder)` | 12 | 48 |  | self.node_manager = node_manager self.database = database |
| 157 | `CacheWarmup.install(self)` | 1 | 22 | 1 | Wire the warm-up into the node manager (idempotent): |
| 180 | `CacheWarmup._on_lifecycle(self, event)` | 2 | 14 | 1 | Lifecycle listener (runs post-lock, so HTTP is safe here). |
| 195 | `CacheWarmup._retry_recovering(self, manager)` | 2 | 20 | 1 | Health-pass hook: retry warm-ups that failed while gated. |
| 216 | `CacheWarmup.recovery_ready(self, node)` | 2 | 19 | **0** | The recovery gate: True when `node` may leave RECOVERING. |
| 240 | `CacheWarmup.ensure(self, node)` | 2 | 54 | 4 | Start a warm-up for `node` unless one is already pending, |
| 295 | `CacheWarmup.status(self)` | 1 | 8 | 5 | Snapshot of every known warm-up record. |
| 304 | `CacheWarmup.forget(self, node)` | 2 | 5 | **0** | Drop a node's warm-up record (Layer 9 scale-down/stop). |
| 310 | `CacheWarmup._entry(self, state, attempts, **extra)` | 4 | 20 | 2 | entry = { entry.update(extra) |
| 335 | `CacheWarmup._run(self, node)` | 2 | 42 | 3 | Thread body: running -> warm_node -> done/failed. |
| 378 | `CacheWarmup.warm_node(self, node, limit)` | 3 | 124 | **0** | Synchronously warm ONE node (Section 10). |
| 507 | `CacheWarmup._db_get(self, key)` | 2 | 7 | **0** | Database read through the circuit breaker when wired. |
| 515 | `CacheWarmup._merge_node_stats(self)` | 1 | 27 | **0** | Fold every ACTIVE node's Layer 6.2 ranking into the cluster |
| 544 | `CacheWarmup._fetch_node_stats(node, limit)` | 2 | 13 | **0** | GET /stats/top-keys from one node. |

### [V] `tui.py` — 1137 lines, 43 functions

| Line | Function | Args | Loc | Calls | Summary |
|---:|---|---:|---:|---:|---|
| 71 | `node_port(url)` | 1 | 2 | 3 | return urlparse(url).port or 80 |
| 75 | `db_path()` | 0 | 7 | 2 | path = os.environ.get("CACHE_DB_PATH", "data/cache.db") if os.path.isabs(path): |
| 84 | `repository()` | 0 | 5 | 6 | path = db_path() return MultiDatasetRepository( |
| 98 | `init_color()` | 0 | 10 | 1 | global USE_COLOR if not sys.stdout.isatty(): |
| 110 | `paint(text, color)` | 2 | 6 | 60 | if not USE_COLOR: return f"\033[{color}m{text}\033[{RESET}" |
| 118 | `clear()` | 0 | 3 | 6 | os.system("cls" if os.name == "nt" else "clear") |
| 127 | `http(method, url, body, timeout)` | 4 | 32 | 14 | Normalized request helper. |
| 161 | `_detail(data)` | 1 | 6 | 1 | if isinstance(data, dict) and "detail" in data: return "HTTP error" |
| 173 | `project_python()` | 0 | 10 | 5 | Prefer the project venv so the windows run the right deps. |
| 185 | `spawn(label, command)` | 2 | 12 | 3 | kwargs = {"cwd": ROOT} if os.name == "nt": |
| 199 | `is_alive(child)` | 1 | 2 | 2 | return child["process"].poll() is None |
| 203 | `alive_children(children)` | 1 | 2 | 1 | return [child for child in children if is_alive(child)] |
| 207 | `ensure_running(children)` | 1 | 32 | 2 | Start anything that is not already answering. Processes already |
| 241 | `_spawned(children, label)` | 2 | 2 | 2 | return any(child["label"] == label for child in children) |
| 245 | `stop_all(children)` | 1 | 28 | 4 | for child in children: deadline = time.time() + 3 |
| 278 | `_emergency_stop()` | 0 | 7 | 1 | if _STOPPED["done"] or not _STOPPED.get("children"): _STOPPED["done"] = True |
| 291 | `wait_for(predicate, label, timeout, interval)` | 4 | 17 | 4 | print(f"  {label} ", end="", flush=True) deadline = time.time() + timeout |
| 310 | `active_nodes()` | 0 | 8 | 2 | status = http("GET", f"{ROUTER_URL}/cluster/status", timeout=1.5) if not status["ok"]: |
| 320 | `auto_seed()` | 0 | 39 | 1 | Import unregistered files from the datasets/ drop folder. |
| 361 | `boot(children)` | 1 | 30 | 1 | clear() print(paint("DCS - Distributed Cache  \|  one-command launcher", BOLD)) |
| 393 | `print_cluster_summary()` | 0 | 19 | 2 | status = http("GET", f"{ROUTER_URL}/cluster/status", timeout=1.5) if not status["ok"]: |
| 414 | `state_color(state)` | 1 | 10 | 2 | if state == "ready": if state in ("starting", "recovering"): |
| 430 | `header(children)` | 1 | 37 | 1 | alive = len(alive_children(children)) total = len(children) |
| 469 | `menu()` | 0 | 22 | 1 | print(""" |
| 493 | `ask(prompt)` | 1 | 6 | 15 | try: |
| 501 | `pause()` | 0 | 7 | 2 | if sys.stdin.isatty(): |
| 510 | `fmt(value, limit)` | 2 | 11 | 2 | if isinstance(value, str): if len(text) > limit: |
| 523 | `render_table(headers, rows)` | 2 | 24 | 4 | def cell(item): headers = [cell(head) for head in headers] |
| 553 | `parse_value(text)` | 1 | 12 | 9 | JSON if it parses, otherwise the raw string. |
| 567 | `compute_verdict(cache_results, db_result)` | 2 | 49 | 9 | cache_results: [(label, status, value)]  status hit\|miss\|down |
| 630 | `action_status(children)` | 1 | 12 | **0** | status = http("GET", f"{ROUTER_URL}/cluster/status") if not status["ok"]: |
| 644 | `action_put(children)` | 1 | 26 | **0** | key = ask("Key (e.g. user:42)") if not key: |
| 672 | `action_get(children)` | 1 | 20 | **0** | key = ask("Key") if not key: |
| 694 | `action_delete(children)` | 1 | 12 | **0** | key = ask("Key") if not key: |
| 708 | `action_db_get(children)` | 1 | 14 | **0** | key = ask("Key") if not key: |
| 724 | `action_resume(children)` | 1 | 14 | **0** | started = ensure_running(children) if not started: |
| 740 | `action_inspect(children)` | 1 | 49 | **0** | key = ask("Key to inspect (e.g. user:42)") if not key: |
| 791 | `_short(value, limit)` | 2 | 7 | 2 | text = value if isinstance(value, str) else json.dumps( return text if len(text) <= limit else text[:limit - 3] + "..." |
| 800 | `action_list_keys(children)` | 1 | 84 | **0** | prefix = ask("Key prefix (blank = dataset + prefix summary)") limit_raw = ask("Limit (blank = 20)") |
| 886 | `action_sql(children)` | 1 | 37 | **0** | store = repository() options = [label for label, _ in store.repos()] |
| 925 | `action_datasets(children)` | 1 | 77 | **0** | store = repository() while True: |
| 1004 | `action_stats(children)` | 1 | 53 | **0** | print() print(paint("  ROUTER", BOLD)) |
| 1078 | `main()` | 0 | 56 | 10 | init_color() children = [] |

---

## Part 4 — Zero external call sites (need manual review)

Excludes endpoints, dunders, and `main`. **Not automatically dead:** methods reached through inheritance (`_LinkedListOps`, `_CacheStats`, `_ConnectionMixin`, `_CrudMixin`, `Flight`, `CircuitBreaker` internals), properties, string-dispatched handlers, and test-only helpers.

| Group | File | Line | Function |
|:--:|---|---:|---|
| W | `_inventory.py` | 176 | `group_key(r)` |
| A | `cache/_linked_list_ops.py` | 8 | `_LinkedListOps._remove_node(self, node)` |
| A | `cache/_linked_list_ops.py` | 27 | `_LinkedListOps._add_to_front(self, node)` |
| A | `cache/_linked_list_ops.py` | 46 | `_LinkedListOps._move_to_front(self, node)` |
| A | `cache/_linked_list_ops.py` | 56 | `_LinkedListOps._remove_lru(self)` |
| A | `cache/lru_cache.py` | 119 | `LRUCache.get_or_set(self, key, loader, ttl)` |
| A | `cache/lru_cache.py` | 167 | `LRUCache.get_entry(self, key)` |
| A | `cache/node.py` | 37 | `Node.is_expired(self, current_time)` |
| B | `cache/statistics.py` | 124 | `CacheStatistics._note_access(self, key)` |
| B | `cache/statistics.py` | 195 | `CacheStatistics.rotate(self, top_keys_limit)` |
| B | `cache/statistics.py` | 275 | `CacheStatistics._rotation_loop(self)` |
| C | `cluster/consistent_hash.py` | 23 | `ConsistentHashRing._hash(self, key)` |
| C | `cluster/consistent_hash.py` | 35 | `ConsistentHashRing.add_node(self, node)` |
| C | `cluster/consistent_hash.py` | 62 | `ConsistentHashRing.remove_node(self, node)` |
| C | `cluster/consistent_hash.py` | 154 | `ConsistentHashRing.copy(self)` |
| C | `cluster/consistent_hash.py` | 170 | `ConsistentHashRing.with_node(self, node)` |
| C | `cluster/consistent_hash.py` | 189 | `ConsistentHashRing.get_all_nodes(self)` |
| C | `cluster/events.py` | 23 | `LifecycleEvent.old(self)` |
| C | `cluster/events.py` | 27 | `LifecycleEvent.new(self)` |
| C | `cluster/health_monitor.py` | 54 | `health_check_loop(manager, checker)` |
| C | `cluster/node_activation.py` | 6 | `activate_node(manager, node)` |
| C | `cluster/node_activation.py` | 37 | `deactivate_node(manager, node)` |
| C | `cluster/node_manager.py` | 112 | `NodeManager.get_primary(self, key)` |
| C | `cluster/node_manager.py` | 140 | `NodeManager.declared_ring_view(self)` |
| C | `cluster/node_manager.py` | 170 | `NodeManager.expected_primary(self, key, node)` |
| C | `cluster/node_manager.py` | 189 | `NodeManager.add_listener(self, callback)` |
| C | `cluster/node_manager.py` | 199 | `NodeManager.remove_listener(self, callback)` |
| C | `cluster/node_manager.py` | 206 | `NodeManager.add_health_hook(self, callback)` |
| C | `cluster/node_manager.py` | 215 | `NodeManager.set_recovery_gate(self, gate)` |
| C | `cluster/node_manager.py` | 270 | `NodeManager.mark_recovering(self, node)` |
| C | `cluster/node_manager.py` | 291 | `NodeManager.mark_failed(self, node)` |
| C | `cluster/node_manager.py` | 300 | `NodeManager.is_available(self, node)` |
| C | `cluster/node_manager.py` | 312 | `NodeManager._mark(self, node, target)` |
| C | `cluster/node_manager.py` | 381 | `NodeManager.get_node_state(self, node)` |
| C | `cluster/node_manager.py` | 394 | `NodeManager.start_health_monitor(self)` |
| C | `cluster/node_manager.py` | 411 | `NodeManager.get_status(self)` |
| D | `database/_connection_mixin.py` | 9 | `_ConnectionMixin._get_connection(self)` |
| D | `database/_connection_mixin.py` | 21 | `_ConnectionMixin._initialize_database(self)` |
| D | `database/multi_repository.py` | 49 | `MultiDatasetRepository._sync(self)` |
| D | `database/multi_repository.py` | 74 | `MultiDatasetRepository._repo_for_key(self, key)` |
| D | `database/multi_repository.py` | 145 | `MultiDatasetRepository.repos(self)` |
| D | `database/registry.py` | 307 | `DatasetRegistry._stat(self)` |
| D | `database/registry.py` | 314 | `DatasetRegistry._load(self)` |
| D | `database/registry.py` | 351 | `DatasetRegistry.names(self)` |
| D | `database/registry.py` | 356 | `DatasetRegistry.dataset_db_path(self, name)` |
| D | `database/registry.py` | 415 | `DatasetRegistry.unregister(self, name, remove_file)` |
| D | `database/registry.py` | 435 | `DatasetRegistry.update(self, name, **fields)` |
| D | `database/registry.py` | 457 | `DatasetRegistry._save(self, datasets)` |
| D | `database/registry.py` | 480 | `DatasetRegistry.prefix_for(self, key)` |
| D | `database/registry.py` | 494 | `DatasetRegistry.entry_for(self, key)` |
| D | `database/registry.py` | 512 | `DatasetRegistry.scan_drop_folder(self, drop_dir)` |
| M | `services/cache_service.py` | 103 | `CacheService._load_from_database(self, key)` |
| M | `services/cache_service.py` | 218 | `CacheService.evict(self, key)` |
| E | `services/circuit_breaker.py` | 69 | `CircuitBreaker.call(self, func, *args, **kwargs)` |
| E | `services/circuit_breaker.py` | 104 | `CircuitBreaker._should_reject(self)` |
| E | `services/circuit_breaker.py` | 126 | `CircuitBreaker._record_success(self)` |
| E | `services/circuit_breaker.py` | 146 | `CircuitBreaker._record_failure(self)` |
| E | `services/circuit_breaker.py` | 171 | `CircuitBreaker.get_state(self)` |
| E | `services/circuit_breaker.py` | 177 | `CircuitBreaker.get_stats(self)` |
| L | `services/dataset_admin.py` | 241 | `DatasetAdmin.after_key_change(self, key)` |
| L | `services/dataset_admin.py` | 266 | `DatasetAdmin.purge_search_caches(self, name)` |
| L | `services/dataset_admin.py` | 311 | `DatasetAdmin._key_for(self, entry, value, repo)` |
| L | `services/dataset_admin.py` | 334 | `DatasetAdmin._next_sequence(repo, prefix)` |
| L | `services/dataset_admin.py` | 351 | `DatasetAdmin._sample_rows(self, repo, limit)` |
| L | `services/dataset_admin.py` | 365 | `DatasetAdmin._sync_stats(self, name)` |
| M | `services/distributed_cache.py` | 139 | `DistributedCache.flush_prefix(self, prefix)` |
| K | `services/process_manager.py` | 48 | `_default_spawner(command, cwd, log_path)` |
| K | `services/process_manager.py` | 112 | `ProcessManager.command(self, port, capacity)` |
| K | `services/process_manager.py` | 120 | `ProcessManager._pid_path(self, port)` |
| K | `services/process_manager.py` | 123 | `ProcessManager._read_pid(self, port)` |
| H | `services/replication.py` | 86 | `ReplicationManager._replicable_store(self)` |
| H | `services/replication.py` | 155 | `ReplicationManager.acked_sequence(self, node)` |
| H | `services/replication.py` | 161 | `ReplicationManager.forget_node(self, node)` |
| H | `services/replication.py` | 171 | `ReplicationManager.drop_prefix(self, prefix)` |
| H | `services/replication.py` | 193 | `ReplicationManager.entries_since(self, after_sequence)` |
| H | `services/replication.py` | 208 | `ReplicationManager.log_oldest_sequence(self)` |
| H | `services/replication.py` | 284 | `ReplicationManager.sync_ready(self, node)` |
| H | `services/replication.py` | 455 | `ReplicationManager._needs_snapshot(self, node, acked, current)` |
| H | `services/replication.py` | 493 | `ReplicationManager._replay_into(self, node, after_sequence)` |
| H | `services/replication.py` | 524 | `ReplicationManager._snapshot_node(self, node)` |
| H | `services/replication.py` | 557 | `ReplicationManager.repair_active_nodes(self, limit)` |
| H | `services/replication.py` | 631 | `ReplicationManager.repair(self)` |
| H | `services/replication.py` | 704 | `ReplicationManager.record_failover(self, failed_node)` |
| H | `services/replication.py` | 719 | `ReplicationManager.last_failover(self)` |
| H | `services/replication.py` | 727 | `ReplicationManager.failovers(self)` |
| I | `services/search.py` | 366 | `SearchService.search(self, entity, params)` |
| I | `services/search.py` | 436 | `SearchService.suggest(self, entity, field, q)` |
| I | `services/search.py` | 513 | `SearchService.browse(self, prefix, limit)` |
| I | `services/search.py` | 632 | `SearchService._where(self, spec, normal)` |
| I | `services/search.py` | 702 | `SearchService._order(self, spec, normal)` |
| I | `services/search.py` | 717 | `SearchService._spec_map(self)` |
| I | `services/search.py` | 744 | `SearchService._repos(self, prefix)` |
| I | `services/search.py` | 751 | `SearchService._spec(self, entity)` |
| I | `services/search.py` | 839 | `SearchService._cache_key(self, entity, normal)` |
| I | `services/search.py` | 864 | `SearchService._int(value, default, minimum, maximum)` |
| I | `services/search.py` | 883 | `SearchService._number(value)` |
| I | `services/search.py` | 897 | `SearchService._text(value)` |
| I | `services/search.py` | 905 | `SearchService._truthy(value)` |
| I | `services/search.py` | 913 | `SearchService._ms(seconds)` |
| E | `services/single_flight.py` | 20 | `Flight.set_result(self, result)` |
| E | `services/single_flight.py` | 25 | `Flight.set_error(self, error)` |
| E | `services/single_flight.py` | 72 | `SingleFlight.do(self, key, function)` |
| E | `services/single_flight.py` | 122 | `SingleFlight.in_flight_keys(self)` |
| J | `services/telemetry.py` | 50 | `RequestTelemetry.record_get(self, source)` |
| J | `services/telemetry.py` | 64 | `RequestTelemetry.record_search(self, hit)` |
| F | `services/warmup.py` | 216 | `CacheWarmup.recovery_ready(self, node)` |
| F | `services/warmup.py` | 304 | `CacheWarmup.forget(self, node)` |
| F | `services/warmup.py` | 378 | `CacheWarmup.warm_node(self, node, limit)` |
| F | `services/warmup.py` | 507 | `CacheWarmup._db_get(self, key)` |
| F | `services/warmup.py` | 515 | `CacheWarmup._merge_node_stats(self)` |
| F | `services/warmup.py` | 544 | `CacheWarmup._fetch_node_stats(node, limit)` |
| V | `tui.py` | 630 | `action_status(children)` |
| V | `tui.py` | 644 | `action_put(children)` |
| V | `tui.py` | 672 | `action_get(children)` |
| V | `tui.py` | 694 | `action_delete(children)` |
| V | `tui.py` | 708 | `action_db_get(children)` |
| V | `tui.py` | 724 | `action_resume(children)` |
| V | `tui.py` | 740 | `action_inspect(children)` |
| V | `tui.py` | 800 | `action_list_keys(children)` |
| V | `tui.py` | 886 | `action_sql(children)` |
| V | `tui.py` | 925 | `action_datasets(children)` |
| V | `tui.py` | 1004 | `action_stats(children)` |