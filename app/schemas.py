from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

class NodeCreate(BaseModel):
    name: str
    address: str
    cpu_capacity: int = 4
    memory_capacity_mb: int = 4096


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    cpu_capacity: int
    memory_capacity_mb: int
    status: str
    last_heartbeat: datetime | None = None

    

class WorkloadCreate(BaseModel):
    name: str
    image: str
    replicas: int = Field(default=1, ge=0, le=20)


class ScaleRequest(BaseModel):
    replicas: int = Field(ge=0, le=20)


class PodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: int | None = None
    container_id: str | None = None
    status: str
    restart_count: int


class WorkloadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    image: str
    replicas: int
    pods: list[PodOut] = []