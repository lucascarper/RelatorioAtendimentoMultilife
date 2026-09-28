"""Endpoints financeiros do SGG contra respostas simuladas (respx)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from relatorio.domain.financeiro import Categoria
from relatorio.infrastructure.sgg.cliente import ClienteSgg
from relatorio.infrastructure.sgg.dto_financeiro import para_decimal
from relatorio.infrastructure.sgg.limitador import LimitadorTaxa

BASE = "https://app.sgg.net.br/api/v3/"
FIXTURES = Path(__file__).parent.parent / "fixtures"


def carregar(nome: str) -> dict[str, Any]:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


@pytest.fixture
def cliente() -> ClienteSgg:
    return ClienteSgg(BASE, "CHAVEDETESTE0123456789abcdefABCD", limitador=LimitadorTaxa(10_000))


@respx.mock
def test_emitidos_com_faturamento_rateio_e_lgpd(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "contasReceber/").mock(
        return_value=httpx.Response(200, json=carregar("contas_receber_emitidas.json"))
    )
    titulos = {t.id: t for t in cliente.receber_emitidos(date(2026, 9, 1), date(2026, 9, 27))}
    params = rota.calls[0].request.url.params
    assert params["dataEmissao_aPartirDe"] == "2026-09-01"
    assert params["dataEmissao_ate"] == "2026-09-27"
    assert params["retornar_faturamento"] == "Simplificado"
    assert set(titulos) == {5001, 5002}  # a conta sem id é descartada

    empresa = titulos[5001]
    assert (empresa.nome, empresa.id_cliente, empresa.classificacao) == (
        "Empresa Exemplo Ltda",
        444,
        "MULTILIFE - GESTAO 2",
    )
    assert empresa.rateio == (("2", Decimal("1200.50")),)
    assert [(i.id_servico, i.categoria, i.qtde) for i in empresa.itens] == [
        (None, Categoria.MENSALIDADES, 1),
        (13, Categoria.COMPLEMENTARES, 5),
    ]
    assert empresa.em_aberto

    pessoa = titulos[5002]
    assert pessoa.nome == "Pessoa física #445"  # nome de pessoa física não é guardado
    assert (pessoa.valor, pessoa.valor_liquidado, pessoa.pagamento) == (
        Decimal("85.00"),
        Decimal("86.10"),
        date(2026, 9, 20),
    )


@respx.mock
def test_periodo_longo_vira_janelas_de_31_dias(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "contasPagar/").mock(
        return_value=httpx.Response(200, json={"resultado": [], "temProximaPagina": False})
    )
    cliente.pagar_a_vencer(date(2026, 9, 1), date(2026, 11, 15))
    janelas = [
        (
            c.request.url.params["dataVencimento_aPartirDe"],
            c.request.url.params["dataVencimento_ate"],
        )
        for c in rota.calls
    ]
    assert janelas == [
        ("2026-09-01", "2026-10-01"),
        ("2026-10-02", "2026-11-01"),
        ("2026-11-02", "2026-11-15"),
    ]


@respx.mock
def test_vencidos_filtra_situacao_pelo_texto(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "contasReceber/").mock(
        return_value=httpx.Response(200, json={"resultado": [], "temProximaPagina": False})
    )
    cliente.receber_vencidos()
    assert rota.calls[0].request.url.params["situacao"] == "Vencida"


@respx.mock
def test_contratos_e_tabela_de_precos(cliente: ClienteSgg) -> None:
    respx.get(BASE + "contratoCliente/").mock(
        return_value=httpx.Response(200, json=carregar("contrato_cliente.json"))
    )
    rota_precos = respx.get(BASE + "fornecedor-valores/").mock(
        return_value=httpx.Response(
            200,
            json={
                "resultado": [
                    {
                        "id_fornecedor": "2",
                        "id_empresa": "",
                        "id_servico": "Audiometria Tonal (0281)",
                        "valor_a_cobrar": "50.00",
                        "valor_a_pagar": "25.00",
                    },
                    {
                        "id_fornecedor": "3",
                        "id_empresa": "77",
                        "id_servico": "Audiometria Tonal (0281)",
                        "valor_a_cobrar": "60.00",
                        "valor_a_pagar": "30.00",
                    },
                ],
                "temProximaPagina": False,
            },
        )
    )
    contratos = cliente.contratos_ativos()
    assert [(c.id, c.id_cliente, c.vencimento, c.excedente_configurado) for c in contratos] == [
        (366, 839, date(2026, 10, 15), True),
        (367, 840, date(2027, 5, 1), False),
    ]
    precos = cliente.precos_servico(11)
    assert rota_precos.calls[0].request.url.params["id_servico"] == "11"
    assert [(p.id_fornecedor, p.id_empresa, p.valor_pagar) for p in precos] == [
        (2, None, Decimal("25.00")),
        (3, 77, Decimal("30.00")),
    ]


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("12.00", "12.00"),
        ("15,00", "15.00"),
        ("1.234,56", "1234.56"),
        ("R$65.00", "65.00"),
        (80, "80"),
        ("", None),
        (None, None),
    ],
)
def test_valores_em_formatos_variados(bruto: object, esperado: str | None) -> None:
    valor = para_decimal(bruto)
    assert (str(valor) if valor is not None else None) == esperado
