# This module re-exports the router's public API so that existing
# test imports (import router.router as router_module) continue
# to work.  All real logic lives in the sibling files.

from .app import app
from ._components import (
    database,
    node_manager,
    distributed_cache,
    cache_service,
    hot_keys,
    warmup,
    replication,
    telemetry,
    search_service,
    configure_cluster,
)

# Import route modules so their @app decorators register the endpoints.
from . import routes_cache  # noqa: F401
from . import routes_db  # noqa: F401
from . import routes_cluster  # noqa: F401
from . import routes_search  # noqa: F401
from . import routes_dashboard  # noqa: F401
from . import routes_manage  # noqa: F401

# Static last: its mount is registered after every API route.
from . import routes_static  # noqa: F401

__all__ = [
    "app",
    "database",
    "node_manager",
    "distributed_cache",
    "cache_service",
    "hot_keys",
    "warmup",
    "replication",
    "telemetry",
    "search_service",
    "configure_cluster",
]
