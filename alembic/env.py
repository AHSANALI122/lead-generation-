"""Alembic environment (F16).

Migrations run against the **sync** database (psycopg / `DATABASE_URL`) — the same
engine the app uses for Lead CRUD. The URL is read from the environment at runtime
(via `.env`) and never stored in `alembic.ini`, so no secret lives in version control.

`target_metadata` is SQLModel's metadata, populated by importing `backend.models`, so
autogenerate sees `Lead` and `DailyUsage`. The OpenAI Agents SDK creates its own
conversation-memory tables (`agent_sessions` / `agent_messages`) at runtime with
`create_tables=True`; those are **not** in our metadata, so `include_name` filters them
out — otherwise autogenerate would try to DROP them.
"""

import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

from alembic import context

# Load .env so DATABASE_URL (and friends) are available when alembic runs from the CLI.
load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Importing the models registers Lead + DailyUsage on SQLModel.metadata for autogenerate.
import backend.models  # noqa: E402,F401

target_metadata = SQLModel.metadata

# Tables the SDK owns and creates itself — never let Alembic manage (or drop) them.
_SDK_TABLES = {"agent_sessions", "agent_messages"}


def _db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set — required to run migrations")
    return url


def include_name(name, type_, parent_names) -> bool:
    """Keep autogenerate focused on our tables; ignore the SDK's memory tables."""
    if type_ == "table":
        return name not in _SDK_TABLES
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL using just the URL)."""
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    connectable = create_engine(_db_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
