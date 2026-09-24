"""Caso de uso genérico de leitura: métricas de qualquer período.

A consolidação noturna usa ``inicio = ontem 00:00`` e ``fim = ontem 23:59``; uma futura
consulta em tempo real usará ``inicio = hoje 00:00`` e ``fim = agora`` — mesmo coletor,
mesma tabela de eventos, mesmo cálculo (seção 7, "Evolução futura").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from relatorio.application.coleta import ResolvedorAgendas
from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.application.ports import FabricaUoW, Relogio
from relatorio.domain.disponibilidade import janelas_sem_coleta
from relatorio.domain.entidades import FUSO_BRASILIA, Agenda
from relatorio.domain.metricas import (
    AgendamentoDoDia,
    FalhaColeta,
    MetricasPeriodo,
    calcular_metricas,
)

JOB_COLETA = "coletar_ciclo"


@dataclass(frozen=True, slots=True)
class ResultadoMetricas:
    metricas: MetricasPeriodo
    unidades: tuple[str, ...]
    configuracao: ConfiguracaoRelatorio


def _dias(inicio: date, fim: date) -> Iterable[date]:
    atual = inicio
    while atual <= fim:
        yield atual
        atual += timedelta(days=1)


def descrever_unidades(
    configuracao: ConfiguracaoRelatorio, agendas: Iterable[Agenda]
) -> tuple[str, ...]:
    if configuracao.filtro.todas_unidades:
        return ("Todas as unidades",)
    nomes = {
        a.id_unidade_atendimento: a.unidade_atendimento or f"Unidade {a.id_unidade_atendimento}"
        for a in agendas
        if a.id_unidade_atendimento is not None
    }
    return tuple(sorted(nomes.get(u, f"Unidade {u}") for u in configuracao.unidades))


class ObterMetricasPeriodo:
    def __init__(
        self,
        uow: FabricaUoW,
        relogio: Relogio,
        configuracao_padrao: ConfiguracaoRelatorio,
        janela: JanelaColeta,
        coletor_habilitado: bool = True,
    ) -> None:
        self._uow = uow
        self._relogio = relogio
        self._padrao = configuracao_padrao
        self._janela = janela
        self._coletor_habilitado = coletor_habilitado

    def configuracao(self) -> ConfiguracaoRelatorio:
        with self._uow() as uow:
            return self._padrao.mesclar(uow.configuracoes.obter_todas())

    def executar(
        self,
        inicio: datetime,
        fim: datetime,
        *,
        faltas_confirmadas: Iterable[int] = (),
        verificacao_faltas_indisponivel: bool = False,
    ) -> ResultadoMetricas:
        dia_inicio = inicio.astimezone(FUSO_BRASILIA).date()
        dia_fim = fim.astimezone(FUSO_BRASILIA).date()
        with self._uow() as uow:
            configuracao = self._padrao.mesclar(uow.configuracoes.obter_todas())
            agendas = uow.agendas.listar()
            snapshots = uow.snapshots.listar_por_data(dia_inicio, dia_fim)
            eventos = uow.eventos.listar_por_agendamentos([s.id_agendamento for s in snapshots])
            sucessos = (
                uow.execucoes.inicios_com_sucesso(
                    JOB_COLETA,
                    self._janela.do_dia(dia_inicio)[0],
                    self._janela.do_dia(dia_fim)[1],
                )
                if self._coletor_habilitado
                else []
            )

        por_id = {a.id_agenda: a for a in agendas}
        resolvedor = ResolvedorAgendas(agendas)
        agendamentos = [
            AgendamentoDoDia(
                id_agendamento=s.id_agendamento,
                id_agenda=s.id_agenda
                if s.id_agenda is not None
                else resolvedor.resolver(s.agenda_nome, s.id_unidade_atendimento),
                agenda_nome=s.agenda_nome,
                id_unidade_atendimento=s.id_unidade_atendimento,
                data_agendamento=s.data_agendamento,
                hora_agendamento=s.hora_agendamento,
                situacao_atual=s.situacao_atual,
                eventos=tuple(eventos.get(s.id_agendamento, ())),
            )
            for s in snapshots
        ]
        filtrados = configuracao.filtro.aplicar(agendamentos, por_id)

        falhas: list[FalhaColeta] = []
        if self._coletor_habilitado:
            agora = self._relogio.agora()
            for dia in _dias(dia_inicio, dia_fim):
                janela_inicio, janela_fim = self._janela.do_dia(dia)
                falhas.extend(janelas_sem_coleta(sucessos, janela_inicio, min(janela_fim, agora)))

        metricas = calcular_metricas(
            filtrados,
            por_id,
            configuracao.regras,
            inicio,
            fim,
            faltas_confirmadas=faltas_confirmadas,
            falhas_coleta=falhas,
            verificacao_faltas_indisponivel=verificacao_faltas_indisponivel,
        )
        return ResultadoMetricas(
            metricas=metricas,
            unidades=descrever_unidades(configuracao, agendas),
            configuracao=configuracao,
        )
