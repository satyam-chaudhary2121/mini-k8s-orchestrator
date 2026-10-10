from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Pod, Workload
from app.schemas import ScaleRequest, WorkloadCreate, WorkloadOut

router = APIRouter(prefix="/workloads", tags=["workloads"])


def get_or_404(db: Session, name: str) -> Workload:
    workload = db.scalar(select(Workload).where(Workload.name == name))
    if not workload:
        raise HTTPException(status_code=404, detail="Workload not found")
    return workload


@router.post("", response_model=WorkloadOut, status_code=201)
def create_workload(data: WorkloadCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Workload).where(Workload.name == data.name)):
        raise HTTPException(status_code=409, detail="Workload already exists")
    workload = Workload(**data.model_dump())
    db.add(workload)
    db.commit()
    db.refresh(workload)
    return workload


@router.get("", response_model=list[WorkloadOut])
def list_workloads(db: Session = Depends(get_db)):
    return list(db.scalars(select(Workload)))


@router.get("/{name}", response_model=WorkloadOut)
def get_workload(name: str, db: Session = Depends(get_db)):
    return get_or_404(db, name)


@router.put("/{name}/scale", response_model=WorkloadOut)
def scale_workload(name: str, data: ScaleRequest, db: Session = Depends(get_db)):
    workload = get_or_404(db, name)
    workload.replicas = data.replicas
    db.commit()
    db.refresh(workload)
    return workload


@router.delete("/{name}")
def delete_workload(name: str, db: Session = Depends(get_db)):
    workload = get_or_404(db, name)
    db.execute(delete(Pod).where(Pod.workload_id == workload.id))
    db.delete(workload)
    db.commit()
    return {"deleted": name}