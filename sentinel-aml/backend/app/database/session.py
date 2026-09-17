"""Engine/session management. PostgreSQL in local+production; SQLite only for fast unit tests."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False},
                            poolclass=StaticPool if ":memory:" in url else None)

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # enforce FKs in SQLite like PostgreSQL does
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return eng
    s = get_settings()
    return create_engine(url, pool_size=s.db_pool_size, max_overflow=5, pool_pre_ping=True, pool_recycle=1800)


def configure(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    _engine = build_engine(url or get_settings().database_url)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure()
    return _engine  # type: ignore[return-value]


def get_sessionmaker() -> sessionmaker:
    if _SessionLocal is None:
        configure()
    return _SessionLocal  # type: ignore[return-value]


def init_db(engine: Engine | None = None) -> None:
    from app.database.models import Base

    eng = engine or get_engine()
    if eng.dialect.name == "postgresql":
        with eng.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(eng)


def get_db() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
