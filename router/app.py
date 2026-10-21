from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Stop the node processes THIS router spawned (Cluster control /
    scale-up) before exit.  Nodes started by another process - the
    TUI - are left alone: their owner stops them.
    """

    yield

    from services.process_manager import process_manager

    try:
        process_manager.stop_all()
    except Exception as error:
        print(f"[SHUTDOWN] could not stop spawned nodes: {error!r}")


app = FastAPI(
    title="Distributed Cache Router",
    description="Layer 5.7/5.8 - Cache-Aside, Database fallback, Circuit Breaker & Single-Flight",
    version="3.1",
    lifespan=lifespan
)
