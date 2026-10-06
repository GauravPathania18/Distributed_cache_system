import sys

from cluster.node_manager import NodeManager
from cluster.node_state import NodeState
from database.repository import DatabaseRepository
from services.cache_service import CacheService
from services.distributed_cache import DistributedCache

from .config import (
    DEFAULT_DB_PATH,
    VIRTUAL_NODES,
    REPLICATION_FACTOR,
    HEALTH_INTERVAL,
    FAILURE_THRESHOLD,
    RECOVERY_THRESHOLD,
    BOOTSTRAP_STATE,
    TIMEOUT,
    nodes_from_env,
)


# Source of truth. Shared by the whole router process.
database = DatabaseRepository(db_path=DEFAULT_DB_PATH)

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

# Layer 5 cache-aside policy.
cache_service = CacheService(
    cache=distributed_cache,
    database=database
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
    """

    global node_manager, distributed_cache, cache_service

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

    cache_service = CacheService(
        cache=distributed_cache,
        database=database
    )

    # Sync the names back into the router.router shim so that tests
    # that do `router_module.configure_cluster(...)` followed by
    # `router_module.cache_service` see the updated objects.
    router_module = sys.modules.get("router.router")
    if router_module is not None:
        router_module.node_manager = node_manager
        router_module.distributed_cache = distributed_cache
        router_module.cache_service = cache_service
