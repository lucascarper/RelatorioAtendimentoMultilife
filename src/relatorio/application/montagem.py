"""Montagem dos casos de uso a partir das portas (um único lugar para a composição)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.coleta import ColetarCiclo, ReconciliarDia, SincronizarAgendas
from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.application.consolidacao import ConsolidarDia
from relatorio.application.envio import EnviarRelatorio, VerificarEnvio
from relatorio.application.manutencao import AlertaFalhasColeta, LimparRetencao, ReprocessarData
from relatorio.application.metricas import ObterMetricasPeriodo
from relatorio.application.ports import (
    EnviadorEmail,
    FabricaUoW,
    Relogio,
    RenderizadorEmail,
    SggGateway,
)


@dataclass(frozen=True, slots=True)
class CasosDeUso:
    alertar: AlertarTecnico
    coletar: ColetarCiclo
    reconciliar: ReconciliarDia
    sincronizar: SincronizarAgendas
    metricas: ObterMetricasPeriodo
    consolidar: ConsolidarDia
    enviar: EnviarRelatorio
    verificar_envio: VerificarEnvio
    reprocessar: ReprocessarData
    limpar: LimparRetencao
    alerta_coleta: AlertaFalhasColeta


def montar_casos_de_uso(
    *,
    uow: FabricaUoW,
    relogio: Relogio,
    sgg: SggGateway,
    email: EnviadorEmail,
    renderizador: RenderizadorEmail,
    configuracao_padrao: ConfiguracaoRelatorio,
    janela: JanelaColeta,
    coletor_habilitado: bool = True,
    destinatarios_override: Sequence[str] = (),
) -> CasosDeUso:
    alertar = AlertarTecnico(uow, email, renderizador, configuracao_padrao)
    reconciliar = ReconciliarDia(sgg, uow, relogio)
    metricas = ObterMetricasPeriodo(uow, relogio, configuracao_padrao, janela, coletor_habilitado)
    consolidar = ConsolidarDia(uow, relogio, metricas, sgg, alertar)
    enviar = EnviarRelatorio(
        uow, relogio, consolidar, renderizador, email, alertar, destinatarios_override
    )
    return CasosDeUso(
        alertar=alertar,
        coletar=ColetarCiclo(sgg, uow, relogio),
        reconciliar=reconciliar,
        sincronizar=SincronizarAgendas(sgg, uow),
        metricas=metricas,
        consolidar=consolidar,
        enviar=enviar,
        verificar_envio=VerificarEnvio(uow, alertar),
        reprocessar=ReprocessarData(consolidar, enviar, reconciliar),
        limpar=LimparRetencao(uow, relogio),
        alerta_coleta=AlertaFalhasColeta(uow, alertar),
    )
