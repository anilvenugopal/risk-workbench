"""Alembic environment — targets the WORKBENCH database only.

The DB URL is resolved via the db/ package (get_connection_config +
build_sqlalchemy_url) so it uses the same env-var resolution as the app.
The URL is set programmatically — NOT via alembic.ini — to avoid ConfigParser
treating percent-encoded ODBC strings as interpolation syntax.

Dev strategy: single revision (0001_initial.py), drop-create-seed per iteration.
No revision accumulation until production cutover.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from db.config import get_connection_config, build_sqlalchemy_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata: import all models here so Alembic sees them.
# Uncomment as models are created in Iteration 0+.
# from app.models import Base  # noqa: F401
# target_metadata = Base.metadata
target_metadata = None


def _workbench_url() -> str:
    cfg = get_connection_config("WORKBENCH")
    return build_sqlalchemy_url(cfg)


def run_migrations_offline() -> None:
    context.configure(
        url=_workbench_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_workbench_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
