"""Alembic environment. Uses DATABASE_URL from .env unless sqlalchemy.url is set on the config."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import Settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
