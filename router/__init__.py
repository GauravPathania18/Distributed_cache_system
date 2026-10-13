"""
Public re-exports: `from router import app, database, ...`.

The real imports happen lazily (PEP 562) so that importing a light
submodule such as router.config does NOT start the health-monitor
thread or open the database - only touching one of these names does.
"""

import importlib


_EXPORTS = {
    "app": (".app", "app"),
    "configure_cluster": ("._components", "configure_cluster"),
    "database": ("._components", "database"),
    "cache_service": ("._components", "cache_service"),
}

__all__ = ["app", "configure_cluster", "database", "cache_service"]


def __getattr__(name):

    target = _EXPORTS.get(name)

    if target is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    module_name, attribute = target

    value = getattr(importlib.import_module(module_name, __name__),
                    attribute)

    globals()[name] = value

    return value


def __dir__():
    return sorted(set(list(globals())) | set(__all__))
