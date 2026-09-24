"""Integração com PostgreSQL: defina TEST_DATABASE_URL (banco descartável).

Ex.: ``TEST_DATABASE_URL=postgresql://relatorio:relatorio@localhost:5432/relatorio_teste``
O esquema é criado pelas próprias migrações do Alembic (testa as migrações também).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from relatorio.application.ports import FabricaUoW, UnidadeDeTrabalho
from relatorio.config import Settings
from relatorio.infrastructure.db.repositorios import UnidadeDeTrabalhoSql, criar_fabrica_sessao

RAIZ = Path(__file__).resolve().parents[2]
# Estado logo após as migrações (antes de qualquer TRUNCATE dos testes).
ESTADO_INICIAL: dict[str, list[str]] = {}
TABELAS = (
    "agenda, agendamento_snapshot, agendamento_evento, resumo_diario, destinatario, "
    "execucao_job, configuracao, coletor_cursor"
)


def _url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    return Settings(database_url=url).database_url if url else None


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL não definida")
    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    motor = create_engine(url, connect_args={"options": "-c timezone=America/Sao_Paulo"})
    with motor.connect() as conexao:
        emails = conexao.execute(text("SELECT email FROM destinatario ORDER BY id")).scalars()
        ESTADO_INICIAL["destinatarios"] = list(emails)
    yield motor
    motor.dispose()


@pytest.fixture
def uow(engine: Engine) -> FabricaUoW:
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {TABELAS} RESTART IDENTITY"))
    fabrica = criar_fabrica_sessao(engine)

    def nova() -> UnidadeDeTrabalho:
        return UnidadeDeTrabalhoSql(fabrica)

    return nova
