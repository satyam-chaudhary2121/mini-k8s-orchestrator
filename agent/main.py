import docker
from docker.errors import NotFound
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Worker Agent")
client = docker.from_env()


class RunRequest(BaseModel):
    name: str
    image: str
    host_port: int | None = None
    container_port: int | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/containers")
def list_containers():
    containers = client.containers.list(all=True)
    return [
        {"id": c.short_id, "name": c.name, "image": c.image.tags, "status": c.status}
        for c in containers
    ]


@app.post("/containers", status_code=201)
def run_container(req: RunRequest):
    ports = None
    if req.host_port and req.container_port:
        ports = {f"{req.container_port}/tcp": req.host_port}
    container = client.containers.run(
        req.image, name=req.name, detach=True, ports=ports
    )
    return {"id": container.short_id, "name": container.name, "status": container.status}


@app.delete("/containers/{name}")
def remove_container(name: str):
    try:
        container = client.containers.get(name)
    except NotFound:
        raise HTTPException(status_code=404, detail="Container not found")
    container.remove(force=True)
    return {"removed": name}