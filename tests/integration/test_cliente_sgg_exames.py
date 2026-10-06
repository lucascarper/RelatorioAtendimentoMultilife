"""Endpoints dos atendimentos por médico no SGG contra respostas simuladas (respx)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
import respx

from relatorio.infrastructure.sgg.cliente import ClienteSgg
from relatorio.infrastructure.sgg.dto_exames import para_exame_clinico, para_medico
from relatorio.infrastructure.sgg.limitador import LimitadorTaxa

BASE = "https://app.sgg.net.br/api/v3/"
DIA = date(2026, 10, 5)


@pytest.fixture
def cliente() -> ClienteSgg:
    return ClienteSgg(BASE, "CHAVEDETESTE0123456789abcdefABCD", limitador=LimitadorTaxa(10_000))


def item(**campos: Any) -> dict[str, Any]:
    base = {
        "id_exames_lancados": "500",
        "id_empresa": "58",
        "id_funcionario": "21068",
        "nome_funcionario": "Fulano de Tal",
        "data_nascimento_funcionario": "1990-01-01",
        "data_exames_lancados": "2026-10-05",
        "tipo_exame": "Periódico",
        "exame": "Clínico",
        "medico": "34985-DF",
        "nome_medico": "Ana Souza",
        "resultado_atestado": "Apto",
        "link_aso": "https://app.sgg.net.br/doc/segredo",
    }
    return {**base, **campos}


def test_dto_guarda_so_o_necessario_e_o_nome_fica_fora_do_repr() -> None:
    exame = para_exame_clinico(item())
    assert (exame.id, exame.data, exame.id_empresa, exame.id_funcionario) == (500, DIA, 58, 21068)
    assert (exame.crm, exame.medico, exame.tipo) == ("34985-DF", "Ana Souza", "Periódico")
    assert exame.funcionario == "Fulano de Tal"
    assert "Fulano" not in repr(exame) and "segredo" not in repr(exame)
    assert para_exame_clinico(item(medico="34985df", tipo_exame="")).crm == "34985-DF"
    assert para_exame_clinico(item(tipo_exame="")).tipo == "Outro"
    for invalido in (item(id_exames_lancados=""), item(data_exames_lancados=""), item(medico="")):
        with pytest.raises(ValueError):
            para_exame_clinico(invalido)
    assert para_medico({"CRM": "123-RS", "nome": "Dr. X"}).crm == "123-RS"


@respx.mock
def test_exames_clinicos_do_dia_filtra_e_ignora_invalidos(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "exames-realizados/").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"resultado": [item(), item(id_exames_lancados="")], "temProximaPagina": True},
            ),
            httpx.Response(
                200,
                json={
                    "resultado": [
                        item(id_exames_lancados="501", data_exames_lancados="2026-10-04")
                    ],
                    "temProximaPagina": False,
                },
            ),
        ]
    )
    exames = cliente.exames_clinicos(DIA)
    assert [e.id for e in exames] == [500]  # o de outro dia fica fora
    params = rota.calls[0].request.url.params
    assert (params["dataExame_aPartirDe"], params["dataExame_ate"], params["exame"]) == (
        "2026-10-05",
        "2026-10-05",
        "Clínico",
    )


@respx.mock
def test_empresa_e_medico_por_codigo(cliente: ClienteSgg) -> None:
    respx.get(BASE + "empresa/").mock(
        return_value=httpx.Response(
            200,
            json={"resultado": {"id_empresa": "58", "nome": "Conplan LTDA", "CNPJ": ""}},
        )
    )
    respx.get(BASE + "medico/").mock(
        return_value=httpx.Response(200, json={"statusCode": "D001", "statusMsg": "em branco"})
    )
    empresa = cliente.empresa(58)
    assert empresa is not None and empresa.nome == "Conplan LTDA"
    assert cliente.medico("1-DF") is None
