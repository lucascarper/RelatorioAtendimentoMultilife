"""Dashboards do financeiro (somas do período, foto do último dia) e do SESMT (última coleta)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from relatorio.domain.comparativo import Sentido, comparar_valor
from relatorio.domain.dashboard_financeiro import CAMPOS, resumir_financeiro
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import (
    DadosSesmt,
    DocumentoSst,
    EmpresaSesmt,
    EventoEsocial,
    calcular_sesmt,
)
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


def sesmt(referencia: date, vencidos: int, rejeitados: int) -> dict[str, Any]:
    """Métricas de uma coleta: ``vencidos`` PGRs vencidos e ``rejeitados`` eventos sem recibo."""
    empresas = {i: EmpresaSesmt(i, f"Empresa {i}", "", "1", True) for i in range(1, 4)}
    documentos = [
        DocumentoSst(1 + i % 3, ("PGR", "PCMSO", "LTCAT")[i % 3], date(2026, 1, 1))
        for i in range(vencidos)
    ]
    documentos.append(DocumentoSst(2, "PGR", date(2026, 12, 10), date(2026, 6, 1)))
    eventos = [EventoEsocial(str(i), "S-2220", 1, referencia, "") for i in range(rejeitados)]
    eventos += [
        EventoEsocial("x", "S-2240", 2, date(2026, 10, 3), "1.1.1"),
        EventoEsocial("y", "S-2240", 2, date(2026, 10, 4), ""),
    ]
    dados = DadosSesmt(
        referencia, date(2026, 11, 18), empresas, documentos, eventos, {"1": "PREMIUM"}
    )
    return calcular_sesmt(dados, datetime(2026, 11, 17, 23, 0, tzinfo=FUSO_BRASILIA))


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


def test_painel_sesmt_mostra_a_ultima_coleta_com_setas_e_historico() -> None:
    atual = sesmt(date(2026, 11, 17), vencidos=3, rejeitados=2)
    anterior = sesmt(date(2026, 11, 16), vencidos=2, rejeitados=1)
    painel = montar_sesmt(atual, anterior, [(date(2026, 11, 16), 2), (date(2026, 11, 17), 3)])
    vencidos, vencem, enviados, rejeitados = painel.cartoes
    assert (vencidos.valor, vencidos.contexto) == ("3", "em 3 empresas")
    assert vencidos.delta is not None and (vencidos.delta.tom, vencidos.delta.rotulo) == (
        "ruim",
        "vs 16/11",
    )
    assert vencem.rotulo == "Vencem em dezembro" and vencem.valor == "1"
    assert rejeitados.delta is not None and rejeitados.delta.rotulo == "vs 01/10 a 17/10"
    assert enviados.delta is not None and enviados.delta.tom == "neutro"
    assert painel.manchete.startswith("3 documentos vencidos em 3 empresas; 1 vence em dezembro.")
    # Barras deitadas por grupo, empilhadas por tipo.
    assert painel.grupos["horizontal"] and painel.grupos["rotulos"] == ["PREMIUM"]
    assert [s["dados"] for s in painel.grupos["series"]] == [[1], [1], [1]]
    assert painel.vencem == [
        {"empresa": "Empresa 2", "cnpj": "", "grupo": "PREMIUM", "documentos": "PGR 10/12"}
    ]
    assert painel.rejeitados[0]["quantidade"] == "2"
    dia, mes = painel.historico
    assert dia["rotulos"] == ["16/11", "17/11"] and dia["series"][0]["dados"] == [2, 3]
    assert mes["rotulos"][-2:] == ["out/26", "nov/26"] and mes["eixo"] == "Mês"


def test_painel_sesmt_sem_coleta_anterior_fica_sem_seta_do_dia() -> None:
    painel = montar_sesmt(sesmt(date(2026, 11, 17), 0, 0), None, [])
    assert painel.cartoes[0].delta is None and painel.cartoes[1].delta is None
    assert painel.grupos["rotulos"] == []


def test_recorte_do_json_como_no_banco() -> None:
    dados = financeiro(1.0, 2.0, 3.0, 4.0)
    recorte = _recortar(dados, CAMPOS)
    assert recorte["caixa"] == dados["caixa"]
    assert recorte["faturamento"] == {"dia": 3.0, "titulos_dia": 3}  # sem o "mes"
    assert _recortar({}, (("alertas", "quantidade"),)) == {"alertas": {"quantidade": None}}
