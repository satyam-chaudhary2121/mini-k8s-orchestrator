from datetime import datetime

from pydantic import BaseModel, ConfigDict


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