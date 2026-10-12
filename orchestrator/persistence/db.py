from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class Database:
    """Owns the engine and hands out transactional sessions (the project's StateStore)."""

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 10) -> None:
        self.engine: Engine = create_engine(
            url, echo=echo, pool_size=pool_size, max_overflow=10, pool_pre_ping=True
        )
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """One unit of work: commit on success, roll back on any exception."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
