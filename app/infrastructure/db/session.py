"""Engine and session factory helpers."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def database_runtime(database_url: str) -> str:
    """Return the supported runtime family for a SQLAlchemy database URL."""
    try:
        backend = make_url(database_url).get_backend_name()
    except Exception as exc:
        raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
    if backend == "sqlite":
        return "sqlite"
    if backend == "postgresql":
        return "postgresql"
    raise ValueError("DATABASE_URL must use SQLite or PostgreSQL")


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    runtime = database_runtime(database_url)
    if runtime == "sqlite":
        url = make_url(database_url)
        engine_options: dict[str, object] = {
            "connect_args": {"check_same_thread": False},
        }
        if url.database in {None, "", ":memory:"}:
            engine_options["poolclass"] = StaticPool
        return create_engine(database_url, echo=echo, future=True, **engine_options)
    return create_engine(
        database_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def database_is_ready(engine: Engine) -> bool:
    """Perform a bounded, non-sensitive database readiness probe."""
    try:
        with engine.connect():
            return True
    except Exception:
        return False


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
