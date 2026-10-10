import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Node

TIMEOUT_SECONDS = 15


def check_nodes_once():
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(seconds=TIMEOUT_SECONDS)
        for node in db.scalars(select(Node)):
            if node.last_heartbeat is None or node.last_heartbeat < cutoff:
                node.status = "NotReady"
        db.commit()
    finally:
        db.close()


def monitor_loop():
    while True:
        try:
            check_nodes_once()
        except Exception as exc:
            print("node monitor error:", exc)
        time.sleep(5)


def start_monitor():
    threading.Thread(target=monitor_loop, daemon=True).start()