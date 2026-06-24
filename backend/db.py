"""Database engines and table creation.

Two engines point at the same Neon database with different drivers:
- a *sync* engine (psycopg) for Lead CRUD from the agent's tools, and
- an *async* engine (asyncpg) for the Agents SDK conversation memory (added in F5).

Engines are created lazily so importing this module never requires a live DB (keeps
imports/tests cheap); they fail loudly with a clear message only when first used.

Neon's pooled endpoint runs through PgBouncer, which is incompatible with asyncpg's
prepared-statement cache, so we disable it (`statement_cache_size=0`).
"""

import os
from functools import lru_cache

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel, create_engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Sync engine (psycopg) for Lead CRUD."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return create_engine(url, echo=False)


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    """Async engine (asyncpg) for the SDK's chat-history session (F5+).

    ssl=True forces TLS to Neon; statement_cache_size=0 is required behind PgBouncer.
    """
    url = os.getenv("ASYNC_DATABASE_URL")
    if not url:
        raise RuntimeError("ASYNC_DATABASE_URL is not set")
    return create_async_engine(
        url,
        echo=False,
        connect_args={"ssl": True, "statement_cache_size": 0},
    )


def create_db_and_tables() -> None:
    """Create all SQLModel tables on the sync engine.

    Importing models here guarantees the Lead table is registered on the metadata
    before create_all runs.

    As of F16, schema is managed by Alembic (`alembic upgrade head`) and the app no
    longer calls this on startup; it's kept for ad-hoc local/test setup only.
    """
    import backend.models  # noqa: F401  (registers tables on SQLModel.metadata)

    SQLModel.metadata.create_all(get_engine())
