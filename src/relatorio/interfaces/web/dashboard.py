"""Dashboard do atendimento: resumo do período → valores prontos para a tela.

Reaproveita os cartões e as setas do e-mail (``montar_delta``). O gráfico diário é
desenhado no navegador pelo Chart.js com os dados que a página leva em JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from relatorio.application.manutencao import subtrair_meses
from relatorio.domain.comparativo import Sentido, comparar, comparar_contagem
from relatorio.domain.dashboard import ResumoPeriodo
from relatorio.domain.exportacao import validar_periodo
from relatorio.domain.resumo import para_json
from relatorio.infrastructure.email.apresentacao import (
    CartaoKpi,
    Delta,
    duracao,
    montar_delta,
    numero,
    percentual,
)


@dataclass(frozen=True, slots=True)
class PainelDashboard:
    periodo: ResumoPeriodo
    anterior: ResumoPeriodo
    cartoes: tuple[CartaoKpi, ...]
    comparacao: str
    grafico: dict[str, Any]


def periodo_padrao(hoje: date) -> tuple[date, date]:
    """Do dia 1º até ontem; no dia 1º, o mês anterior inteiro (o de hoje ainda não tem dado)."""
    ontem = hoje - timedelta(days=1)
    return ontem.replace(day=1), ontem


def escolher_periodo(inicio: str, fim: str, hoje: date) -> tuple[date, date, str | None]:
    """Período pedido na URL; se inválido, o padrão e a mensagem de erro para a tela."""
    if not inicio and not fim:
        return (*periodo_padrao(hoje), None)
    try:
        de, ate = date.fromisoformat(inicio), date.fromisoformat(fim)
    except ValueError:
        return (*periodo_padrao(hoje), "Informe a data inicial e a data final.")
    try:
        validar_periodo(de, ate, hoje)  # máximo de 31 dias, sem data futura
    except ValueError as erro:
        return (*periodo_padrao(hoje), str(erro))
    return de, ate, None


def periodo_anterior(inicio: date, fim: date) -> tuple[date, date]:
    """O mesmo intervalo no mês anterior (1–27/out → 1–27/set)."""
    return subtrair_meses(inicio, 1), subtrair_meses(fim, 1)


def montar_dashboard(atual: ResumoPeriodo, anterior: ResumoPeriodo) -> PainelDashboard:
    base = None if anterior.vazio else anterior
    comparativo = para_json(comparar(atual.kpis, base.kpis if base else None, anterior.inicio))
    comparativo["itens"].append(
        para_json(
            comparar_contagem(
                "alertas",
                "Alertas",
                atual.alertas,
                base.alertas if base else None,
                Sentido.MENOR_MELHOR,
            )
        )
    )
    intervalo = f"{anterior.inicio:%d/%m} a {anterior.fim:%d/%m}"

    def delta(chave: str) -> Delta | None:
        d = montar_delta(comparativo, chave)
        return replace(d, rotulo=f"vs {intervalo}") if d else None

    k = atual.kpis
    cartoes = (
        CartaoKpi(
            rotulo="Atendimentos realizados",
            valor=numero(k.atendimentos),
            contexto=f"de {numero(k.agendados)} agendados",
            delta=delta("atendimentos"),
        ),
        CartaoKpi(
            rotulo="Faltas",
            valor=numero(k.faltas),
            contexto=f"{percentual(k.taxa_faltas)} dos agendados",
            delta=delta("taxa_faltas"),
        ),
        CartaoKpi(
            rotulo="Espera média",
            valor=duracao(k.espera_media_s),
            contexto=f"maior espera {duracao(k.espera_maxima_s)}",
            delta=delta("espera_media_s"),
        ),
        CartaoKpi(
            rotulo="TMA geral",
            valor=duracao(k.tma_s),
            contexto=f"{numero(k.atendimentos_medidos)} atendimentos medidos",
            delta=delta("tma_s"),
        ),
        CartaoKpi(
            rotulo="Tempo Médio Total de Permanência",
            valor=duracao(k.permanencia_total_s),
            contexto=(
                "da chegada na recepção até a saída do consultório"
                if k.permanencia_total_s is not None
                else "só com guichês marcados nas agendas"
            ),
            delta=delta("permanencia_total_s"),
        ),
        CartaoKpi(
            rotulo="Alertas",
            valor=numero(atual.alertas),
            contexto="somados nos dias do período",
            delta=delta("alertas"),
        ),
    )
    return PainelDashboard(
        periodo=atual,
        anterior=anterior,
        cartoes=cartoes,
        comparacao=(
            f"comparado com {intervalo}"
            if base
            else "sem dados consolidados no mesmo período do mês anterior"
        ),
        grafico={
            "rotulos": [f"{d.data:%d/%m}" for d in atual.dias],
            "atendimentos": [d.atendimentos for d in atual.dias],
            "faltas": [d.faltas for d in atual.dias],
        },
    )
