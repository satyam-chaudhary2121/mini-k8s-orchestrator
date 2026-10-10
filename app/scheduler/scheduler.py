from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Node, Pod


def pick_node(db: Session, nodes: list[Node]) -> Node | None:
    """Least-loaded scheduling: choose the Ready node running the fewest pods."""
    best = None
    best_count = None
    for node in nodes:
        count = db.scalar(
            select(func.count())
            .select_from(Pod)
            .where(Pod.node_id == node.id, Pod.status.in_(["Pending", "Running"]))
        )
        if best is None or count < best_count:
            best, best_count = node, count
    return best