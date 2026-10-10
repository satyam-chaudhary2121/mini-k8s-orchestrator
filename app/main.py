from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import nodes, workloads
from app.controllers.node_monitor import start_monitor
from app.controllers.workload_controller import start_controller


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_monitor()
    start_controller()
    yield


app = FastAPI(title="Mini K8s Orchestrator", lifespan=lifespan)
app.include_router(nodes.router)
app.include_router(workloads.router)


@app.get("/health")
def health():
    return {"status": "ok"}