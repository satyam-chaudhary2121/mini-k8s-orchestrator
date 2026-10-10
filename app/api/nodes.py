from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Node
from app.schemas import NodeCreate, NodeOut

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.post("", response_model=NodeOut, status_code=201)
def register_node(data: NodeCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Node).where(Node.name == data.name))
    if existing:
        raise HTTPException(status_code=409, detail="Node already exists")
    node = Node(**data.model_dump())
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


@router.get("", response_model=list[NodeOut])
def list_nodes(db: Session = Depends(get_db)):
    return list(db.scalars(select(Node)))