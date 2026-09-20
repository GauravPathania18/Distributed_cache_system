from fastapi import FastAPI


app = FastAPI(
    title="Distributed Cache Router",
    description="Layer 5.7/5.8 - Cache-Aside, Database fallback, Circuit Breaker & Single-Flight",
    version="3.1"
)
