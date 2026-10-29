"""Dashboards do financeiro e do SESMT: somas do período, fotos do último dia e setas."""

from __future__ import annotations

from datetime import date
from typing import Any

from relatorio.domain.comparativo import Sentido, comparar_valor
from relatorio.domain.dashboard_financeiro import CAMPOS, resumir_financeiro
from relatorio.domain.dashboard_sesmt import resumir_sesmt
from relatorio.infrastructure.memoria import _recortar
from relatorio.interfaces.web.dashboard import montar_financeiro, montar_sesmt

INICIO, FIM = date(2026, 10, 1), date(2026, 10, 3)


def financeiro(recebido: float, pago: float, faturado: float, mrr: float) -> dict[str, Any]:
    return {
        "caixa": {
            "recebido_dia": recebido,
            "pago_dia": pago,
            "titulos_recebidos_dia": 2,
            "titulos_pagos_dia": 1,
            "recebido_mes": 999.0,  # "mês até a data": não entra na soma
        },
        "faturamento": {"dia": faturado, "titulos_dia": 3, "mes": 9999.0},
        "recorrente": {"mrr": mrr, "clientes_mrr": 4, "contratos_ativos": 10},
        "contratos_a_vencer": [{"mensalidade": 100.0}, {"mensalidade": 50.5}],
    }


def sesmt(com: int, sem: int, a_vencer: int, vencidos: int, **tipos: int) -> dict[str, Any]:
    return {
        "esocial": {
            "total": com + sem,
            "com_recibo": com,
            "sem_recibo": sem,
            "empresas": 2,
            "por_evento": tipos,
        },
        "a_vencer": {"total": a_vencer, "vencendo": 1, "contratos": 1, "documentos": a_vencer - 1},
        "vencidos": {"total": vencidos, "contratos": vencidos, "documentos": 0},
    }


def test_financeiro_soma_os_dias_e_tira_a_foto_do_ultimo() -> None:
    p = resumir_financeiro(
        INICIO,
        FIM,
        [
            (date(2026, 10, 2), financeiro(500.0, 800.0, 300.0, mrr=2000.0)),
            (date(2026, 10, 1), financeiro(1000.0, 200.0, 100.0, mrr=1500.0)),
        ],
    )
    assert (p.recebido, p.pago, p.saldo, p.faturamento) == (1500.0, 1000.0, 500.0, 400.0)
    assert (p.titulos_recebidos, p.titulos_pagos, p.titulos_faturados) == (4, 2, 6)
    assert p.foto is not None and p.foto.data == date(2026, 10, 2)  # o último, não o primeiro
    assert (p.foto.mrr, p.foto.contratos_a_vencer, p.foto.mensalidades_a_vencer) == (
        2000.0,
        2,
        150.5,
    )
    # Saldo acumulado dia a dia; dia sem resumo vira lacuna.
    assert [d.saldo_acumulado for d in p.dias] == [800.0, 500.0, None]
    assert p.ultimo_dia == date(2026, 10, 2) and not p.vazio


def test_financeiro_vazio() -> None:
    p = resumir_financeiro(INICIO, FIM, [])
    assert p.vazio and p.foto is None and p.saldo == 0
    painel = montar_financeiro(p, p)
    assert [g.titulo for g in painel.grupos] == ["No período"]  # sem foto, sem o 2º grupo


def test_painel_financeiro_compara_periodo_e_foto() -> None:
    atual = resumir_financeiro(INICIO, FIM, [(date(2026, 10, 2), financeiro(400, 100, 0, 1100))])
    anterior = resumir_financeiro(
        date(2026, 9, 1), date(2026, 9, 3), [(date(2026, 9, 2), financeiro(200, 150, 0, 1000))]
    )
    painel = montar_financeiro(atual, anterior)
    periodo, foto = painel.grupos
    assert periodo.comparacao == "comparado com 01/09 a 03/09"
    recebido, pago, saldo, _ = periodo.cartoes
    assert recebido.delta is not None and (recebido.delta.seta, recebido.delta.tom) == ("▲", "bom")
    assert pago.delta is not None and (pago.delta.seta, pago.delta.tom) == ("▼", "bom")
    assert saldo.delta is not None and saldo.delta.rotulo == "vs 01/09 a 03/09"
    assert foto.titulo == "Em 02/10" and foto.comparacao.endswith("comparado com 02/09")
    assert foto.cartoes[0].delta is not None and foto.cartoes[0].delta.texto == "+10,0%"
    assert painel.graficos[0]["formato"] == "moeda"


def test_variacao_com_base_negativa_tem_o_sinal_certo() -> None:
    item = comparar_valor("saldo", "Saldo", 50.0, -100.0, Sentido.MAIOR_MELHOR)
    assert (item.direcao, item.avaliacao, item.variacao) == ("alta", "positiva", 1.5)


def test_sesmt_soma_esocial_e_tira_a_foto_das_pendencias() -> None:
    p = resumir_sesmt(
        INICIO,
        FIM,
        [
            (date(2026, 10, 1), sesmt(5, 1, a_vencer=8, vencidos=2, S2220=4, S2240=2)),
            (date(2026, 10, 3), sesmt(3, 2, a_vencer=6, vencidos=3, S2220=5)),
        ],
    )
    assert (p.eventos, p.com_recibo, p.sem_recibo, p.empresas) == (11, 8, 3, 4)
    assert p.por_evento == (("S2220", 9), ("S2240", 2))
    assert p.foto is not None and (p.foto.data, p.foto.a_vencer, p.foto.vencidos) == (
        date(2026, 10, 3),
        6,
        3,
    )
    assert [(d.com_recibo, d.a_vencer) for d in p.dias] == [(5, 8), (None, None), (3, 6)]


def test_painel_sesmt_setas_e_tabela() -> None:
    atual = resumir_sesmt(INICIO, FIM, [(date(2026, 10, 1), sesmt(5, 1, 8, 2, S2220=6))])
    anterior = resumir_sesmt(
        date(2026, 9, 1), date(2026, 9, 3), [(date(2026, 9, 1), sesmt(5, 0, 4, 2))]
    )
    painel = montar_sesmt(atual, anterior)
    periodo, foto = painel.grupos
    eventos, _, sem_recibo, _ = periodo.cartoes
    assert eventos.delta is not None and eventos.delta.tom == "neutro"
    assert sem_recibo.delta is None  # base zero: sem variação percentual
    assert foto.cartoes[0].delta is not None and foto.cartoes[0].delta.tom == "ruim"
    assert foto.cartoes[0].contexto == "1 contratos · 7 documentos"
    assert painel.tabela is not None and painel.tabela["linhas"] == [("S2220", "6")]
    assert [g["empilhado"] for g in painel.graficos] == [True, False]


def test_recorte_do_json_como_no_banco() -> None:
    dados = financeiro(1.0, 2.0, 3.0, 4.0)
    recorte = _recortar(dados, CAMPOS)
    assert recorte["caixa"] == dados["caixa"]
    assert recorte["faturamento"] == {"dia": 3.0, "titulos_dia": 3}  # sem o "mes"
    assert _recortar({}, (("alertas", "quantidade"),)) == {"alertas": {"quantidade": None}}
