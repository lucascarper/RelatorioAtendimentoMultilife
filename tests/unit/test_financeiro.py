"""Relatório financeiro: regras do glossário (caixa, competência, projeção, MRR…)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.financeiro import (
    Categoria,
    Contrato,
    DadosFinanceiros,
    ItemFaturado,
    PrecoFornecedor,
    Titulo,
    calcular_financeiro,
    categorizar,
    custo_medio,
    inicio_janela_emissao,
    servicos_para_margem,
)

REF = date(2026, 9, 27)  # domingo: o relatório de segunda traz o domingo
HOJE = date(2026, 9, 28)
GERADO = datetime(2026, 9, 28, 5, 45, tzinfo=FUSO_BRASILIA)


def d(texto: str) -> date:
    return date.fromisoformat(texto)


def titulo(id_: int, valor: str, **campos: object) -> Titulo:
    base: dict[str, object] = {
        "situacao": "Aguardando pagamento",
        "cancelada": False,
        "emissao": None,
        "vencimento": None,
        "pagamento": None,
    }
    base.update(campos)
    return Titulo(id=id_, valor=Decimal(valor), **base)  # type: ignore[arg-type]


def item(servico: str, qtde: int, valor: str, id_servico: int | None = None) -> ItemFaturado:
    return ItemFaturado(id_servico=id_servico, servico=servico, qtde=qtde, valor=Decimal(valor))


def dados(**campos: object) -> DadosFinanceiros:
    base: dict[str, object] = {
        "referencia": REF,
        "hoje": HOJE,
        "recebidos_mes": [],
        "pagos_mes": [],
        "emitidos": [],
        "a_receber": [],
        "a_pagar": [],
        "contratos": [],
    }
    base.update(campos)
    return DadosFinanceiros(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("servico", "categoria"),
    [
        ("Outro", Categoria.MENSALIDADES),
        ("PLANO GESTÃO PREMIUM - MULTILIFE", Categoria.MENSALIDADES),
        ("Exame Clínico/\xa0Físico", Categoria.CLINICOS),
        ("Atestado Multilife", Categoria.CLINICOS),
        ("Hemograma Completo (0693)", Categoria.COMPLEMENTARES),
        ("Audiometria Tonal", Categoria.COMPLEMENTARES),
        ("PGR/LTCAT/PCMSO", Categoria.PROGRAMAS),
        ("AET COM TREINAMENTO NR17", Categoria.PROGRAMAS),
        ("Caracterização (PNE) (9999)", Categoria.PROGRAMAS),
        ("Cobrança por falta em atendimento", Categoria.FALTAS),
        ("PALESTRA", Categoria.OUTROS),
    ],
)
def test_categorias_dos_servicos_do_sgg(servico: str, categoria: str) -> None:
    assert categorizar(servico) == categoria


def test_caixa_usa_valor_cobrado_e_separa_dia_e_mes() -> None:
    r = calcular_financeiro(
        dados(
            recebidos_mes=[
                # Pago com atraso: entrou o valor cobrado (com juros), não o do título.
                titulo(1, "217.34", valor_cobrado=Decimal("222.87"), pagamento=REF),
                titulo(2, "100.00", valor_cobrado=None, pagamento=d("2026-09-10")),
                titulo(3, "50.00", pagamento=REF, cancelada=True),
            ],
            pagos_mes=[titulo(10, "80.00", valor_cobrado=Decimal("80.00"), pagamento=REF)],
        ),
        GERADO,
    )
    c = r["caixa"]
    assert (c["recebido_dia"], c["pago_dia"], c["saldo_dia"]) == (222.87, 80.0, 142.87)
    assert (c["recebido_mes"], c["saldo_mes"]) == (322.87, 242.87)
    assert (c["titulos_recebidos_dia"], c["titulos_pagos_dia"]) == (1, 1)


def test_projecao_de_entradas_e_saidas_por_horizonte() -> None:
    r = calcular_financeiro(
        dados(
            a_receber=[
                titulo(1, "100.00", vencimento=HOJE),
                titulo(2, "200.00", vencimento=d("2026-10-04")),  # dia 7 (inclusive)
                titulo(3, "400.00", vencimento=d("2026-10-05")),  # dia 8
                titulo(4, "999.00", vencimento=d("2026-10-01"), pagamento=d("2026-09-27")),
            ],
            a_pagar=[titulo(10, "150.00", vencimento=d("2026-10-20"))],
        ),
        GERADO,
    )
    p = {linha["dias"]: linha for linha in r["projecao"] if not linha["fim_mes"]}
    assert (p[7]["entradas"], p[7]["saidas"], p[7]["titulos_entrada"]) == (300.0, 0.0, 2)
    assert (p[30]["entradas"], p[30]["saidas"], p[30]["saldo"]) == (700.0, 150.0, 550.0)
    (fim_mes,) = [linha for linha in r["projecao"] if linha["fim_mes"]]
    assert fim_mes["ate"] == "2026-09-30"  # HOJE é 28/09: só faltam 3 dias no mês
    assert (fim_mes["entradas"], fim_mes["saidas"], fim_mes["titulos_entrada"]) == (100.0, 0.0, 1)


def test_faturamento_por_competencia_e_categorias() -> None:
    r = calcular_financeiro(
        dados(
            emitidos=[
                titulo(
                    1,
                    "150.00",
                    emissao=REF,
                    itens=(
                        item("Exame Clínico/ Físico", 3, "90.00"),
                        item("Hemograma Completo (0693)", 2, "60.00"),
                    ),
                ),
                titulo(2, "1000.00", emissao=d("2026-09-05"), itens=(item("Outro", 1, "1000.00"),)),
                titulo(
                    3, "500.00", emissao=REF, cancelada=True, itens=(item("Outro", 1, "500.00"),)
                ),
                titulo(4, "80.00", emissao=d("2026-08-30"), itens=(item("Outro", 1, "80.00"),)),
            ]
        ),
        GERADO,
    )
    f = r["faturamento"]
    assert (f["dia"], f["titulos_dia"], f["mes"], f["titulos_mes"]) == (150.0, 1, 1150.0, 2)
    categorias = {c["categoria"]: (c["qtde"], c["valor"], c["ticket"]) for c in f["categorias"]}
    assert categorias[Categoria.CLINICOS] == (3, 90.0, 30.0)
    assert categorias[Categoria.MENSALIDADES] == (1, 1000.0, 1000.0)
    assert f["ajuste_mes"] == 0.0


def test_mrr_so_de_clientes_com_contrato_ativo_nos_ultimos_30_dias() -> None:
    emitidos = [
        titulo(
            1,
            "1000.00",
            emissao=d("2026-09-05"),
            id_cliente=10,
            itens=(item("Outro", 1, "1000.00"),),
        ),
        titulo(
            2, "400.00", emissao=d("2026-08-29"), id_cliente=11, itens=(item("Outro", 1, "400.00"),)
        ),
        titulo(
            3, "900.00", emissao=d("2026-08-27"), id_cliente=11, itens=(item("Outro", 1, "900.00"),)
        ),
        titulo(
            4, "300.00", emissao=d("2026-09-10"), id_cliente=99, itens=(item("Outro", 1, "300.00"),)
        ),
    ]
    contratos = [
        Contrato(1, 10, d("2027-01-01"), "Em andamento"),
        Contrato(2, 11, d("2026-10-10"), "Em andamento", excedente_configurado=True),
    ]
    r = calcular_financeiro(dados(emitidos=emitidos, contratos=contratos), GERADO)
    rec = r["recorrente"]
    # 27/08 fica fora da janela de 30 dias (29/08 a 27/09); o cliente 99 não tem contrato.
    assert (rec["mrr"], rec["clientes_mrr"], rec["contratos_ativos"]) == (1400.0, 2, 2)
    assert (rec["mensalidades_mes"], rec["contratos_com_excedente"]) == (1300.0, 1)
    assert inicio_janela_emissao(REF) == d("2026-08-29")


def test_contratos_a_vencer_nos_proximos_30_dias() -> None:
    contratos = [
        Contrato(1, 10, d("2026-10-10"), "Em andamento"),
        Contrato(2, 11, d("2026-11-15"), "Em andamento"),
        Contrato(3, 12, d("2026-09-30"), "Em andamento"),
    ]
    emitidos = [
        titulo(
            1,
            "500.00",
            emissao=d("2026-09-05"),
            id_cliente=10,
            nome="Empresa Dez",
            itens=(item("Outro", 1, "500.00"),),
        )
    ]
    r = calcular_financeiro(dados(contratos=contratos, emitidos=emitidos), GERADO)
    assert [
        (c["id"], c["cliente"], c["dias"], c["mensalidade"]) for c in r["contratos_a_vencer"]
    ] == [
        (3, "Cliente #12", 2, 0.0),
        (1, "Empresa Dez", 12, 500.0),
    ]


def test_margem_pela_tabela_de_precos_dos_credenciados() -> None:
    emitidos = [
        titulo(
            1,
            "300.00",
            emissao=d("2026-09-10"),
            itens=(
                item("Audiometria Tonal (0281)", 6, "300.00", id_servico=11),
                item("Outro", 1, "0.00"),
            ),
        )
    ]
    precos = {
        11: [
            PrecoFornecedor(11, 2, None, Decimal("50"), Decimal("25")),
            PrecoFornecedor(11, 15, None, Decimal("50"), Decimal("0")),  # próprio: fora da média
            PrecoFornecedor(11, 3, None, Decimal("50"), Decimal("15")),
            PrecoFornecedor(11, 3, 77, Decimal("60"), Decimal("40")),  # preço de um cliente
        ]
    }
    r = calcular_financeiro(dados(emitidos=emitidos, precos=precos), GERADO)
    (linha,) = r["margem"]
    assert (linha["qtde"], linha["faturado"], linha["custo_medio"], linha["custo"]) == (
        6,
        300.0,
        20.0,
        120.0,
    )
    assert (linha["margem"], linha["margem_pct"]) == (180.0, 0.6)
    assert servicos_para_margem(emitidos) == [11]
    assert custo_medio([PrecoFornecedor(1, 1, None, Decimal("9"), Decimal("0"))]) is None


def test_rateio_por_centro_de_custo_e_classificacao() -> None:
    r = calcular_financeiro(
        dados(
            recebidos_mes=[
                titulo(1, "100.00", pagamento=REF, rateio=(("2", Decimal("100.00")),)),
                titulo(2, "40.00", pagamento=REF),
            ],
            pagos_mes=[
                titulo(
                    10,
                    "70.00",
                    pagamento=REF,
                    classificacao="ADMINISTRATIVAS",
                    rateio=(("2", Decimal("30")), ("3", Decimal("40"))),
                ),
            ],
            nomes_centros={"2": "Clínica"},
        ),
        GERADO,
    )
    centros = {c["centro"]: (c["recebido"], c["pago"]) for c in r["rateio"]["centros"]}
    assert centros == {
        "Clínica": (100.0, 30.0),
        "Centro de custo 3": (0.0, 40.0),
        "Sem centro de custo": (40.0, 0.0),
    }
    assert r["rateio"]["despesas_por_classificacao"] == [
        {"classificacao": "ADMINISTRATIVAS", "valor": 70.0, "titulos": 1}
    ]


def test_sem_alteracao_desde_ontem() -> None:
    contratos = [Contrato(1, 10, d("2027-01-01"), "Em andamento")]
    ontem = calcular_financeiro(dados(contratos=contratos), GERADO)
    emitidos = [
        titulo(
            1,
            "300.00",
            emissao=d("2026-09-10"),
            itens=(item("Audiometria Tonal (0281)", 6, "300.00", id_servico=11),),
        )
    ]
    precos = {11: [PrecoFornecedor(11, 2, None, Decimal("50"), Decimal("25"))]}
    hoje = calcular_financeiro(
        dados(contratos=contratos, emitidos=emitidos, precos=precos),
        GERADO,
        anterior=ontem,
    )
    assert "contratos_ativos" in hoje["sem_alteracao"]
    assert "margem" not in hoje["sem_alteracao"]
    assert calcular_financeiro(dados(), GERADO)["sem_alteracao"] == []
