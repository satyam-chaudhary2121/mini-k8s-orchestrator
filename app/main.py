from fastapi import FastAPI

from app.api import nodes

app = FastAPI(title="Mini K8s Orchestrator")
app.include_router(nodes.router)


@app.get("/health")
def health():
    return {"status": "ok"}