from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tothemoon_api import db_models as _db_models  # noqa: F401
from tothemoon_api.config import get_settings
from tothemoon_api.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def _ensure_postgres_alembic_version_width(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(128) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
            """
        )
    )
    connection.execute(
        text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_postgres_alembic_version_width(connection)
        if connection.in_transaction():
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
