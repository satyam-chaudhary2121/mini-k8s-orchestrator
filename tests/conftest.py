from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from orchestrator.api.app import create_app
from orchestrator.config.settings import Settings
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import Base

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg2://orch:orch@localhost:5432/orch_test"
)
KEYS = {
    "admin": "admin-key",
    "operator": "operator-key",
    "readonly": "readonly-key",
    "worker": "worker-key",
}


def _postgres_available() -> bool:
    try:
        with create_engine(TEST_DB_URL).connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres() -> str:
    if not _postgres_available():
        pytest.skip("PostgreSQL not reachable (set TEST_DATABASE_URL)")
    return TEST_DB_URL


@pytest.fixture
def settings(postgres: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=postgres,
        admin_api_key=KEYS["admin"],
        operator_api_key=KEYS["operator"],
        read_only_api_key=KEYS["readonly"],
        worker_api_key=KEYS["worker"],
        run_controllers=False,
        restart_backoff_base=0.0,
        node_unhealthy_after=30,
        node_timeout=60,
        log_level="INFO",
    )  # type: ignore[call-arg]


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    database = Database(settings.database_url)
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    yield database
    database.dispose()


@pytest.fixture
def app(settings: Settings, db: Database):  # type: ignore[no-untyped-def]
    return create_app(settings, db)


@pytest.fixture
def client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app, headers={"Authorization": f"Bearer {KEYS['admin']}"}) as c:
        yield c


def headers_for(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {KEYS[role]}"}


@pytest.fixture
def cluster(app, client, db, settings):  # type: ignore[no-untyped-def]
    from tests.support.cluster import Cluster

    return Cluster(app, client, db, settings)
