"""Paginação padrão das listas do admin contra o PostgreSQL."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from relatorio.application.modelos import ColetaExames
from relatorio.domain.entidades import FUSO_BRASILIA, Agenda
from relatorio.domain.exames import Medico
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


def _financeiro(uow: object, n: int) -> None:
    uow.destinatarios_financeiro.adicionar(f"fin{n:02d}@multilife.com.br", None)  # type: ignore[attr-defined]


def _sesmt(uow: object, n: int) -> None:
    uow.destinatarios_sesmt.adicionar(f"ses{n:02d}@multilife.com.br", None)  # type: ignore[attr-defined]


def _usuarios(uow: object, n: int) -> None:
    uow.usuarios.criar(f"Pessoa {n:02d}", f"pessoa{n:02d}", "x", [])  # type: ignore[attr-defined]


def _medicos(uow: object, n: int) -> None:
    uow.medicos.adicionar(Medico(f"{1000 + n}-DF", f"Dr. Nome {n:02d}"))  # type: ignore[attr-defined]


def _coletas(uow: object, n: int) -> None:
    dia = date(2026, 1, 1) + timedelta(days=n)
    uow.exames.registrar_coleta(  # type: ignore[attr-defined]
        ColetaExames(dia, datetime(2026, 1, 1, 23, 20, tzinfo=FUSO_BRASILIA), 5, 3, ())
    )


def _agendas(uow: object, n: int) -> None:
    uow.agendas.sincronizar([Agenda(n + 1, f"Agenda {n:02d}", None, 1, "Unidade")])  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("url", "preencher", "chave"),
    [
        ("/admin/financeiro", _financeiro, "destinatarios"),
        ("/admin/sesmt", _sesmt, "destinatarios"),
        ("/admin/configuracoes/usuarios", _usuarios, "usuarios"),
        ("/admin/configuracoes/medicos", _medicos, "medicos"),
        ("/admin/agendas", _agendas, "agendas"),
    ],
)
def test_demais_listas_paginam(
    cliente: TestClient,  # noqa: F811
    container: Container,  # noqa: F811
    url: str,
    preencher: object,
    chave: str,
) -> None:
    entrar(cliente)
    with container.uow() as uow:
        for n in range(30):
            preencher(uow, n)  # type: ignore[operator]
        uow.commit()
    pagina = cliente.get(url).text
    assert f'name="{chave}_n"' in pagina
    assert f'id="{chave}"' in pagina
    assert "1–25 de" in pagina
    assert f'hx-target="#{chave}"' in pagina
    assert "hx-include" not in pagina
    assert "26–" in cliente.get(f"{url}?{chave}_p=2").text


def test_parametros_estranhos_nao_entram_nos_links(
    cliente: TestClient,  # noqa: F811
    container: Container,  # noqa: F811
) -> None:
    entrar(cliente)
    _cadastrar(container, 30)
    pagina = cliente.get("/admin/atendimento?foo=bar&outra_p=2&x_n=1e1&destinatarios_n=%2B10").text
    assert "foo=bar" not in pagina
    assert "x_n" not in pagina
    assert "outra_p=2" in pagina
    assert "1–25 de 30" in pagina  # "+10" não vale: volta ao tamanho padrão


def test_adicionar_mantem_pagina_e_tamanho(
    cliente: TestClient,  # noqa: F811
    container: Container,  # noqa: F811
) -> None:
    entrar(cliente)
    _cadastrar(container, 30)
    resposta = cliente.post(
        "/admin/destinatarios",
        data={"email": "novo@multilife.com.br", "nome": "", "csrf": csrf(cliente)},
        headers={
            "referer": "http://testserver/admin/atendimento?destinatarios_n=10&destinatarios_p=2"
        },
        follow_redirects=False,
    )
    assert resposta.headers["location"] == (
        "/admin/atendimento?destinatarios_n=10&destinatarios_p=2#destinatarios"
    )


def test_ultimos_processamentos_paginam(
    cliente: TestClient,  # noqa: F811
    container: Container,  # noqa: F811
) -> None:
    entrar(cliente)
    with container.uow() as uow:
        for n in range(20):
            _coletas(uow, n)
        uow.commit()
    pagina = cliente.get("/admin/configuracoes/medicos?coletas_n=10").text
    assert "1–10 de 14" in pagina  # a tela guarda os 14 processamentos mais recentes
    assert 'hx-target="#coletas"' in pagina
