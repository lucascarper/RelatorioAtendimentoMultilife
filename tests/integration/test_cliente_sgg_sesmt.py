"""Endpoints do SESMT no SGG contra respostas simuladas (respx)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
import respx

from relatorio.infrastructure.sgg.cliente import ClienteSgg
from relatorio.infrastructure.sgg.dto_sesmt import para_empresa
from relatorio.infrastructure.sgg.erros import ErroSggRequisicao
from relatorio.infrastructure.sgg.limitador import LimitadorTaxa

BASE = "https://app.sgg.net.br/api/v3/"
XML = (
    "<?xml version='1.0'?><eSocial><evtMonit Id='ID1497553340000002026092912183714300'>"
    "<ideVinculo><cpfTrab>13372222679</cpfTrab></ideVinculo></evtMonit></eSocial>"
)


@pytest.fixture
def cliente() -> ClienteSgg:
    return ClienteSgg(BASE, "CHAVEDETESTE0123456789abcdefABCD", limitador=LimitadorTaxa(10_000))


def resposta(itens: list[dict[str, Any]], mais: bool = False) -> httpx.Response:
    return httpx.Response(200, json={"resultado": itens, "temProximaPagina": mais})


def sem_dados() -> httpx.Response:
    return httpx.Response(200, json={"statusCode": "D001", "statusMsg": "em branco"})


def test_empresa_pessoa_fisica_e_cnpj_de_mentira_nao_vazam() -> None:
    juridica = para_empresa(
        {
            "id_empresa": "5",
            "nome": "Conplan Sistemas LTDA",
            "CNPJ": "49.755.334/0001-64",
            "id_grupo": "2",
            "situacao_esocial": "Habilitada",
        }
    )
    assert (juridica.nome, juridica.cnpj, juridica.id_grupo, juridica.esocial_habilitado) == (
        "Conplan Sistemas LTDA",
        "49.755.334/0001-64",
        "2",
        True,
    )
    pessoa = para_empresa({"id_empresa": "6", "nome": "João da Silva", "CPF": "123.456.789-09"})
    assert (pessoa.nome, pessoa.cnpj, pessoa.esocial_habilitado) == ("Empresa #6", "", False)
    cpf_no_cnpj = para_empresa({"id_empresa": "7", "nome": "Maria", "CNPJ": "123.456.789-09"})
    assert cpf_no_cnpj.nome == "Empresa #7"
    placeholder = para_empresa({"id_empresa": "8", "nome": "Interna", "CNPJ": "00.000.000/0000-00"})
    assert (placeholder.nome, placeholder.cnpj) == ("Interna", "")
    with pytest.raises(ValueError, match="sem id"):
        para_empresa({"nome": "x"})


@respx.mock
def test_empresas_paginam_e_ignoram_item_invalido(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "empresa/").mock(
        side_effect=[
            resposta([{"id_empresa": "1", "nome": "A", "id_grupo": "1"}, {"nome": "sem id"}], True),
            resposta([{"id_empresa": "2", "nome": "B", "situacao_esocial": "Habilitada"}]),
        ]
    )
    empresas = cliente.empresas_sesmt()
    assert [(e.id, e.esocial_habilitado) for e in empresas] == [(1, False), (2, True)]
    assert [c.request.url.params["paginador[pagina]"] for c in rota.calls] == ["0", "1"]


@respx.mock
def test_eventos_esocial_guardam_so_tipo_data_e_recibo(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "getEvtEsocial/").mock(
        return_value=resposta(
            [
                {
                    "codigo": "14300",
                    "tipo_evento": "S-2220",
                    "id_empresa": "58",
                    "id_funcionario": "21068",
                    "data_geracao": "2026-09-29",
                    "prazo": "2026-10-15",
                    "recibo": "1.1.0000000029071530828",
                    "protocolo": "1.1.202411.0000000010896433990",
                    "xml": XML,
                },
                {
                    "codigo": "14301",
                    "tipo_evento": "S-2240",
                    "id_empresa": "58",
                    "id_funcionario": "21069",
                    "data_geracao": "2026-09-29",
                    "recibo": "",
                    "protocolo": "",
                    "xml": "",
                },
            ]
        )
    )
    eventos = {e.codigo: e for e in cliente.eventos_esocial(58)}
    params = rota.calls[0].request.url.params
    assert (params["id_empresa"], params["evento"]) == ("58", "Todos")
    com, sem = eventos["14300"], eventos["14301"]
    assert sem.rejeitado and not com.rejeitado
    assert (com.tipo, com.id_empresa, com.data_geracao) == ("S-2220", 58, date(2026, 9, 29))
    assert "13372222679" not in repr(eventos)  # o CPF do XML não é guardado
    assert "21068" not in repr(eventos)  # nem o código do funcionário


@respx.mock
def test_campo_obrigatorio_ausente_e_erro_de_requisicao(cliente: ClienteSgg) -> None:
    respx.get(BASE + "getEvtEsocial/").mock(
        return_value=httpx.Response(
            200, json={"statusCode": "H0004", "statusMsg": "Campo evento é obrigatório."}
        )
    )
    with pytest.raises(ErroSggRequisicao, match="evento"):
        cliente.eventos_esocial(1)


@respx.mock
def test_empresa_sem_eventos_no_esocial_nao_e_erro(cliente: ClienteSgg) -> None:
    respx.get(BASE + "getEvtEsocial/").mock(
        return_value=httpx.Response(
            200,
            json={
                "statusCode": "G0010",
                "statusMsg": "Nenhum problema ocorrido no tratamento da API. Mas resultado é vazio",
            },
        )
    )
    assert cliente.eventos_esocial(96) == []
