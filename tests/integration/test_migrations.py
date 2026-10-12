"""The Alembic migrations must build exactly the schema the SQLAlchemy models describe."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).parents[2]


def _reset(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def test_upgrade_matches_models_and_downgrade_is_clean(postgres, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres)
    _reset(postgres)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models and migrations have drifted
    engine = create_engine(postgres)
    tables = set(inspect(engine).get_table_names())
    assert {
        "nodes",
        "deployments",
        "pods",
        "containers",
        "services",
        "service_endpoints",
        "events",
        "resource_allocations",
        "health_checks",
    } <= tables
    pod_indexes = {i["name"] for i in inspect(engine).get_indexes("pods")}
    assert {"ix_pods_node_status", "ix_pods_status"} <= pod_indexes
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()
    _reset(postgres)
