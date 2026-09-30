"""Endpoints do SESMT no SGG contra respostas simuladas (respx)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import pytest
import respx

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import TIPOS_DOCUMENTO
from relatorio.infrastructure.sgg.cliente import ClienteSgg
from relatorio.infrastructure.sgg.dto_sesmt import hora_de_geracao, para_empresa
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


def test_hora_de_geracao_vem_do_id_do_xml_e_precisa_bater_com_o_dia() -> None:
    esperado = datetime(2026, 9, 29, 12, 18, 37, tzinfo=FUSO_BRASILIA)
    assert hora_de_geracao(XML, date(2026, 9, 29)) == esperado
    assert hora_de_geracao(XML, date(2026, 9, 28)) is None  # data de outro dia: descarta
    assert hora_de_geracao("", date(2026, 9, 29)) is None
    assert hora_de_geracao("<x Id='ID1234'/>", None) is None


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
def test_contratos_das_duas_situacoes_com_ultimo(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "contratoCliente/").mock(
        side_effect=[
            resposta(
                [
                    {
                        "id": "10",
                        "id_cliente": "5",
                        "codigo_interno": "2025-089",
                        "situacao_contrato": "Em andamento",
                        "data_vencimento": "2026-10-28",
                        "ultimo": "Sim",
                    }
                ]
            ),
            resposta(
                [
                    {
                        "id": "11",
                        "id_cliente": "5",
                        "situacao_contrato": "Vencido",
                        "data_vencimento": "2024-01-02",
                        "ultimo": "Não",
                    }
                ]
            ),
        ]
    )
    contratos = {c.id: c for c in cliente.contratos_sesmt()}
    assert [c.request.url.params["situacao_contrato"] for c in rota.calls] == [
        "Em andamento",
        "Vencido",
    ]
    assert (contratos[10].codigo, contratos[10].ultimo, contratos[10].vencimento) == (
        "2025-089",
        True,
        date(2026, 10, 28),
    )
    assert contratos[11].ultimo is False


@respx.mock
def test_documentos_uma_consulta_por_tipo_com_a_empresa(cliente: ClienteSgg) -> None:
    def por_tipo(request: httpx.Request) -> httpx.Response:
        tipo = request.url.params["tipo"]
        if tipo == "LTCAT":
            return sem_dados()  # D001: empresa sem esse documento
        return resposta(
            [
                {
                    "codigo": "92" if tipo == "PGR" else "71",
                    "id_empresa": "124",
                    "tipo": tipo,
                    "data_vencimento": "2026-12-14",
                    "situacao": "LIBERADO",
                    "url": "https://app.sgg.net.br/doc/segredo",
                    "nome_emissor": "João Pedro",
                }
            ]
        )

    rota = respx.get(BASE + "programasLaudos/").mock(side_effect=por_tipo)
    documentos = cliente.documentos_sst(124)
    assert sorted(c.request.url.params["tipo"] for c in rota.calls) == sorted(TIPOS_DOCUMENTO)
    assert {c.request.url.params["id_empresa"] for c in rota.calls} == {"124"}
    assert sorted((d.tipo, d.vencimento) for d in documentos) == [
        ("PCMSO", date(2026, 12, 14)),
        ("PGR", date(2026, 12, 14)),
    ]
    assert "segredo" not in repr(documentos)  # o link do documento não é guardado


@respx.mock
def test_eventos_esocial_descartam_o_xml_e_guardam_hora_e_recibo(cliente: ClienteSgg) -> None:
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
    assert com.transmitido and not sem.transmitido
    assert com.gerado_em == datetime(2026, 9, 29, 12, 18, 37, tzinfo=FUSO_BRASILIA)
    assert sem.gerado_em is None
    assert (com.id_funcionario, com.prazo) == (21068, date(2026, 10, 15))
    assert "13372222679" not in repr(eventos)  # o CPF do XML não é guardado


@respx.mock
def test_campo_obrigatorio_ausente_e_erro_de_requisicao(cliente: ClienteSgg) -> None:
    respx.get(BASE + "programasLaudos/").mock(
        return_value=httpx.Response(
            200, json={"statusCode": "H0004", "statusMsg": "Campo tipo é obrigatório."}
        )
    )
    with pytest.raises(ErroSggRequisicao, match="tipo"):
        cliente.documentos_sst(1)


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
