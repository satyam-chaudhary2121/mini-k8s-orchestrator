from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import nodes
from app.controllers.node_monitor import start_monitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_monitor()
    yield


app = FastAPI(title="Mini K8s Orchestrator", lifespan=lifespan)
app.include_router(nodes.router)


@app.get("/health")
def health():
    return {"status": "ok"}