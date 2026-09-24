"""Ponta a ponta (seção 15): dia simulado → coleta → PostgreSQL → consolidação → e-mail.

O oráculo abaixo faz o papel da "conferência manual em planilha": parte da linha do
tempo verdadeira de cada agendamento fictício e aplica só o modelo de observação (o
coletor vê, a cada janela, a última mudança de status). As contagens precisam bater
exatamente e os tempos médios dentro de ±1 min (critério de aceite da v1) — aqui,
sem falha de coleta, eles batem ao segundo.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from relatorio.application.ports import FabricaUoW
from relatorio.domain.entidades import FUSO_BRASILIA, Situacao
from relatorio.infrastructure.email.renderizador import RenderizadorJinja
from relatorio.infrastructure.sgg.simulado import AgendamentoSimulado
from relatorio.interfaces.demo import montar_ambiente, simular_dias
from tests.integration.test_email import RAIZ

pytestmark = pytest.mark.integration

DIA = date(2026, 9, 23)
LIMITE_MIN, LIMITE_MAX = 60, 180 * 60


def _instante(hora: time) -> datetime:
    return datetime.combine(DIA, hora, tzinfo=FUSO_BRASILIA)


OBSERVACOES = [_instante(time(6, 0)) + timedelta(minutes=m) for m in range(12 * 60 + 1)] + [
    _instante(time(18, 30)),
    _instante(time(23, 0)),
]


def transicoes_observadas(
    ag: AgendamentoSimulado,
) -> list[tuple[datetime, Situacao | None, Situacao]]:
    """O que o coletor enxerga: em cada janela de observação, só a última mudança."""
    por_janela: dict[int, tuple[datetime, Situacao]] = {}
    for quando, situacao in ag.linha_do_tempo:
        if quando < _instante(time(0, 0)):
            continue  # criação em dias anteriores: fora do cursor do dia
        janela = bisect_left(OBSERVACOES, quando)
        por_janela[janela] = (quando, situacao)
    observadas = []
    anterior: Situacao | None = None
    for _, (quando, situacao) in sorted(por_janela.items()):
        observadas.append((quando, anterior, situacao))
        anterior = situacao
    return observadas


def oraculo(agendamentos: list[AgendamentoSimulado]) -> dict[str, Any]:
    finais = [ag.situacao_final for ag in agendamentos]
    esperas: list[float] = []
    duracoes: list[float] = []
    for ag in agendamentos:
        obs = transicoes_observadas(ag)
        final = ag.situacao_final
        chegada = next((q for q, _, s in obs if s is Situacao.AGUARDANDO), None)
        chamada = next(
            (
                q
                for q, a, s in obs
                if s is Situacao.EM_ATENDIMENTO
                and a is Situacao.AGUARDANDO
                and chegada is not None
                and q >= chegada
            ),
            None,
        )
        if final is not Situacao.CANCELADO and chegada and chamada:
            esperas.append((chamada - chegada).total_seconds())
        fim = next((t for t in reversed(obs) if t[2] is Situacao.ATENDIDO), None)
        if final is Situacao.ATENDIDO and fim and fim[1] is Situacao.EM_ATENDIMENTO:
            inicio = max(q for q, _, s in obs if s is Situacao.EM_ATENDIMENTO and q <= fim[0])
            duracao = (fim[0] - inicio).total_seconds()
            if LIMITE_MIN <= duracao <= LIMITE_MAX:
                duracoes.append(duracao)
    agendados = sum(1 for f in finais if f is not Situacao.CANCELADO)
    return {
        "agendados": agendados,
        "cancelados": finais.count(Situacao.CANCELADO),
        "atendimentos": finais.count(Situacao.ATENDIDO),
        "faltas": finais.count(Situacao.FALTOU),
        "sem_baixa": sum(1 for f in finais if f in (Situacao.AGENDADO, Situacao.AGUARDANDO)),
        "em_atendimento_aberto": finais.count(Situacao.EM_ATENDIMENTO),
        "espera_media_s": round(statistics.fmean(esperas)),
        "espera_maxima_s": round(max(esperas)),
        "tma_s": round(statistics.fmean(duracoes)),
        "atendimentos_medidos": len(duracoes),
    }


@pytest.fixture
def renderizador() -> RenderizadorJinja:
    return RenderizadorJinja(RAIZ / "templates", RAIZ / "static", "https://admin.exemplo")


def test_dia_simulado_bate_com_a_conferencia(
    uow: FabricaUoW, engine: Engine, renderizador: RenderizadorJinja
) -> None:
    ambiente = montar_ambiente([DIA], uow, renderizador, falha=None, engine=engine)
    with uow() as u:
        u.destinatarios.adicionar("glauco@multilife.com.br", "Glauco")
        u.commit()
    simular_dias([DIA], ambiente.casos, ambiente.executor, ambiente.relogio)

    with uow() as u:
        registro = u.resumos.obter(DIA)
    assert registro is not None
    m = registro.metricas
    esperado = oraculo(ambiente.agendamentos)
    k = m["kpis"]
    for chave in (
        "agendados",
        "cancelados",
        "atendimentos",
        "faltas",
        "sem_baixa",
        "espera_media_s",
        "espera_maxima_s",
        "tma_s",
        "atendimentos_medidos",
    ):
        assert k[chave] == esperado[chave], chave
    assert m["alertas"]["em_atendimento_aberto"]["total"] == esperado["em_atendimento_aberto"]
    assert m["alertas"]["falhas_coleta"] == []
    # Casos especiais do simulador aparecem onde devem.
    assert {a["motivo"] for a in m["alertas"]["atipicos"]} == {"menos de 1 min", "mais de 180 min"}
    assert m["alertas"]["faltas_confirmadas_na_agenda"]["total"] == 1
    assert m["alertas"]["sem_tempo_medido"]["total"] >= 1  # o salto de status

    # Envio às 07:59: e-mail com os números do resumo.
    ambiente.relogio.instante = _instante(time(7, 59)) + timedelta(days=1)
    resultado = ambiente.casos.enviar.executar(DIA)
    assert resultado["status"] == "enviado"
    [(destinatarios, conteudo)] = ambiente.email.enviados
    assert destinatarios == ["glauco@multilife.com.br"]
    assert conteudo.assunto == "Resumo de Atendimentos — 23/09/2026 (quarta-feira)"
    assert f">{k['atendimentos']}<" in conteudo.html
    assert ambiente.casos.enviar.executar(DIA)["status"] == "ja_enviado"


def test_falha_de_coleta_aparece_e_contagens_seguem_exatas(
    uow: FabricaUoW, engine: Engine, renderizador: RenderizadorJinja
) -> None:
    ambiente = montar_ambiente([DIA], uow, renderizador, engine=engine)
    simular_dias([DIA], ambiente.casos, ambiente.executor, ambiente.relogio)
    with uow() as u:
        registro = u.resumos.obter(DIA)
        execucoes = u.execucoes.recentes_por_job(por_job=1000)
    assert registro is not None
    m = registro.metricas
    esperado = oraculo(ambiente.agendamentos)
    for chave in ("agendados", "atendimentos", "faltas", "sem_baixa", "cancelados"):
        assert m["kpis"][chave] == esperado[chave], chave
    [falha] = m["alertas"]["falhas_coleta"]
    assert (falha["inicio"][11:16], falha["fim"][11:16], falha["minutos"]) == ("10:12", "10:40", 28)
    falhas = [e for e in execucoes["coletar_ciclo"] if e.status.value == "falha"]
    assert len(falhas) == 28
    # 10 falhas seguidas → alerta técnico (uma única vez por sequência).
    alertas = [c.assunto for _, c in ambiente.email.enviados]
    assert alertas == ["[Alerta] Relatório de Atendimentos — Coleta do SGG falhando"]
