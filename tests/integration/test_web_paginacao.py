"""Paginação padrão das listas do admin contra o PostgreSQL."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from relatorio.infrastructure.container import Container
from tests.integration.test_web import (  # noqa: F401
    cliente,
    container,
    csrf,
    entrar,
    relogio,
)

pytestmark = pytest.mark.integration


def _cadastrar(container: Container, quantos: int) -> None:  # noqa: F811
    with container.uow() as uow:
        for n in range(quantos):
            uow.destinatarios.adicionar(f"pessoa{n:02d}@multilife.com.br", None)
        uow.commit()


def test_lista_curta_nao_tem_rodape(cliente: TestClient, container: Container) -> None:  # noqa: F811
    entrar(cliente)
    _cadastrar(container, 5)
    assert 'class="paginacao"' not in cliente.get("/admin/atendimento").text


def test_lista_longa_pagina_e_escolhe_tamanho(
    cliente: TestClient,  # noqa: F811
    container: Container,  # noqa: F811
) -> None:
    entrar(cliente)
    _cadastrar(container, 30)
    pagina = cliente.get("/admin/atendimento").text
    assert "1–25 de 30" in pagina
    assert "pessoa24@" in pagina
    assert "pessoa25@" not in pagina
    assert 'name="destinatarios_n"' in pagina
    segunda = cliente.get("/admin/atendimento?destinatarios_p=2").text
    assert "26–30 de 30" in segunda
    assert "pessoa25@" in segunda
    curta = cliente.get("/admin/atendimento?destinatarios_n=10").text
    assert "1–10 de 30" in curta
    assert "pessoa10@" not in curta
