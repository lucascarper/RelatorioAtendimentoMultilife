"""Ambiente do Alembic: a URL do banco vem de DATABASE_URL (pydantic-settings)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from relatorio.config import Settings
from relatorio.infrastructure.db.modelos import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = config.get_main_option("sqlalchemy.url") or Settings().database_url
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = Base.metadata


def executar_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def executar_online() -> None:
    conectavel = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with conectavel.connect() as conexao:
        context.configure(connection=conexao, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            # web e worker rodam `alembic upgrade head` no pre-deploy ao mesmo tempo:
            # a trava faz o segundo esperar o primeiro e então não ter nada a fazer.
            conexao.execute(text("SELECT pg_advisory_xact_lock(hashtext('alembic_migracoes'))"))
            context.run_migrations()


if context.is_offline_mode():
    executar_offline()
else:
    executar_online()
