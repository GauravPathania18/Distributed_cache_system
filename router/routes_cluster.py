from .app import app
from . import _components


@app.get("/cluster/status")
def cluster_status():

    return _components.node_manager.get_status()


@app.get("/circuit-breaker/status")
def circuit_breaker_status():

    return _components.cache_service.circuit_breaker_status()


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "router"
    }
