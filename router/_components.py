import sys

from cache.hot_keys import HotKeyManager
from cluster.node_manager import NodeManager
from cluster.node_state import NodeState
from database import DatasetRegistry, MultiDatasetRepository
from services.cache_service import CacheService
from services.distributed_cache import DistributedCache
from services.replication import ReplicationManager
from services.search import SearchService
from services.seeding import CacheSeeder
from services.telemetry import RequestTelemetry
from services.warmup import CacheWarmup

from .config import (
    DEFAULT_DB_PATH,
    VIRTUAL_NODES,
    REPLICATION_FACTOR,
    HEALTH_INTERVAL,
    FAILURE_THRESHOLD,
    RECOVERY_THRESHOLD,
    BOOTSTRAP_STATE,
    TIMEOUT,
    WARMUP_ENABLED,
    WARMUP_LIMIT,
    WARMUP_RATE_LIMIT,
    HOTKEY_MAX_KEYS,
    SEED_ENABLED,
    SEED_STRATEGY,
    SEED_FILL_RATIO,
    SEED_PER_DATASET,
    SEED_FALLBACK_CAPACITY,
    REPLICATION_ENABLED,
    REPLICATION_LOG_SIZE,
    SYNC_SNAPSHOT_THRESHOLD,
    SEARCH_TTL,
    SEARCH_PAGE_SIZE_DEFAULT,
    SEARCH_PAGE_SIZE_MAX,
    nodes_from_env,
)


# Source of truth. Shared by the whole router process.
# The registry mirrors data/registry.json (mtime-cached), so datasets
# imported by the TUI / import_dataset.py become visible to search
# without restarting the router.
registry = DatasetRegistry(db_path=DEFAULT_DB_PATH)

database = MultiDatasetRepository(db_path=DEFAULT_DB_PATH, registry=registry)

# Production bootstrap gates nodes (STARTING -> RECOVERING -> READY)
# so cold nodes never receive traffic before they prove healthy.
node_manager = NodeManager(
    nodes=nodes_from_env(),
    virtual_nodes=VIRTUAL_NODES,
    replication_factor=REPLICATION_FACTOR,
    health_interval=HEALTH_INTERVAL,
    failure_threshold=FAILURE_THRESHOLD,
    initial_state=BOOTSTRAP_STATE,
    recovery_threshold=RECOVERY_THRESHOLD
)

node_manager.start_health_monitor()

# Data-plane client over the cluster.
distributed_cache = DistributedCache(
    node_manager=node_manager,
    timeout=TIMEOUT
)

# Layer 6.3 - cluster-wide hot-key ranking (router view).
hot_keys = HotKeyManager(max_keys=HOTKEY_MAX_KEYS)

# Layer 7.3 - replication log + sequence coordinator.
replication = ReplicationManager(
    node_manager=node_manager,
    distributed_cache=distributed_cache,
    database=database,
    log_size=REPLICATION_LOG_SIZE,
    snapshot_threshold=SYNC_SNAPSHOT_THRESHOLD,
    enabled=REPLICATION_ENABLED
)

# Layer 8 - cache-vs-database read accounting.
telemetry = RequestTelemetry()

# Layer 5 cache-aside policy (also records into hot_keys +
# replication log + telemetry).
cache_service = CacheService(
    cache=distributed_cache,
    database=database,
    hot_keys=hot_keys,
    replication=replication,
    telemetry=telemetry
)

# Layer 8 - cached search: every unique query is a cache entry.
search_service = SearchService(
    cache_service=cache_service,
    distributed_cache=distributed_cache,
    single_flight=cache_service.single_flight,
    circuit_breaker=cache_service.circuit_breaker,
    database=database,
    telemetry=telemetry,
    ttl=SEARCH_TTL,
    page_size_default=SEARCH_PAGE_SIZE_DEFAULT,
    page_size_max=SEARCH_PAGE_SIZE_MAX,
    registry=registry
)

# Layer 6.5 - cold-start seeding: what an EMPTY node holds when
# warm-up has no traffic history to reload. None = seeding off.
def _make_seeder(repo):
    """Build the seed pool for `repo`, or None when disabled."""

    if not SEED_ENABLED:
        return None

    return CacheSeeder(
        database=repo,
        strategy=SEED_STRATEGY,
        per_dataset=SEED_PER_DATASET,
        fill_ratio=SEED_FILL_RATIO,
        fallback_capacity=SEED_FALLBACK_CAPACITY
    )


# Layer 6.3 - warm-up service: triggers on RECOVERING, retries on
# each health pass after a failure, and gates READY until done.
# Layer 6.5: the seeder only speaks when the hot-key ranking is
# empty (fresh boot) - see services/warmup.py.
warmup = CacheWarmup(
    node_manager=node_manager,
    database=database,
    hot_key_manager=hot_keys,
    distributed_cache=distributed_cache,
    circuit_breaker=cache_service.circuit_breaker,
    enabled=WARMUP_ENABLED,
    limit=WARMUP_LIMIT,
    rate_limit=WARMUP_RATE_LIMIT,
    seeder=_make_seeder(database)
)

warmup.install()

# Layer 7.3 - replication sync triggers on RECOVERING and retries
# on each health pass, exactly like warm-up.
replication.install()

# Layer 6.4 + 7.3 - the recovery gate requires BOTH warm-up done
# AND replication sync done before a node may leave RECOVERING.
# Set last so it composes both halves.
node_manager.set_recovery_gate(
    lambda node: (
        warmup.recovery_ready(node) and replication.sync_ready(node)
    )
)


def configure_cluster(
    nodes,
    replication_factor=REPLICATION_FACTOR,
    virtual_nodes=VIRTUAL_NODES,
    health_interval=HEALTH_INTERVAL,
    failure_threshold=FAILURE_THRESHOLD,
    initial_state=NodeState.READY,
    recovery_threshold=RECOVERY_THRESHOLD,
    start_monitor=False
):
    """
    Rebuild the cluster components (used by tests / demos).

    Tests and demos bootstrap as READY so they can route traffic
    immediately without waiting for the health monitor. The
    production wiring above uses the gated BOOTSTRAP_STATE instead.

    Hot-key ranking and warm-up status are rebuilt too, so every
    configure_cluster() call starts from a clean Layer 6.3 slate.
    """

    global node_manager, distributed_cache, cache_service, hot_keys, warmup
    global replication, telemetry, search_service

    node_manager = NodeManager(
        nodes=nodes,
        virtual_nodes=virtual_nodes,
        replication_factor=replication_factor,
        health_interval=health_interval,
        failure_threshold=failure_threshold,
        initial_state=initial_state,
        recovery_threshold=recovery_threshold
    )

    if start_monitor:
        node_manager.start_health_monitor()

    distributed_cache = DistributedCache(
        node_manager=node_manager,
        timeout=TIMEOUT
    )

    hot_keys = HotKeyManager(max_keys=HOTKEY_MAX_KEYS)

    replication = ReplicationManager(
        node_manager=node_manager,
        distributed_cache=distributed_cache,
        database=database,
        log_size=REPLICATION_LOG_SIZE,
        snapshot_threshold=SYNC_SNAPSHOT_THRESHOLD,
        enabled=REPLICATION_ENABLED
    )

    telemetry = RequestTelemetry()

    cache_service = CacheService(
        cache=distributed_cache,
        database=database,
        hot_keys=hot_keys,
        replication=replication,
        telemetry=telemetry
    )

    search_service = SearchService(
        cache_service=cache_service,
        distributed_cache=distributed_cache,
        single_flight=cache_service.single_flight,
        circuit_breaker=cache_service.circuit_breaker,
        database=database,
        telemetry=telemetry,
        ttl=SEARCH_TTL,
        page_size_default=SEARCH_PAGE_SIZE_DEFAULT,
        page_size_max=SEARCH_PAGE_SIZE_MAX,
        registry=registry
    )

    warmup = CacheWarmup(
        node_manager=node_manager,
        database=database,
        hot_key_manager=hot_keys,
        distributed_cache=distributed_cache,
        circuit_breaker=cache_service.circuit_breaker,
        enabled=WARMUP_ENABLED,
        limit=WARMUP_LIMIT,
        rate_limit=WARMUP_RATE_LIMIT,
        seeder=_make_seeder(database)
    )

    warmup.install()
    replication.install()

    node_manager.set_recovery_gate(
        lambda node: (
            warmup.recovery_ready(node) and replication.sync_ready(node)
        )
    )

    # Sync the names back into the router.router shim so that tests
    # that do `router_module.configure_cluster(...)` followed by
    # `router_module.cache_service` see the updated objects.
    router_module = sys.modules.get("router.router")
    if router_module is not None:
        router_module.node_manager = node_manager
        router_module.distributed_cache = distributed_cache
        router_module.cache_service = cache_service
        router_module.hot_keys = hot_keys
        router_module.warmup = warmup
        router_module.replication = replication
        router_module.telemetry = telemetry
        router_module.search_service = search_service
