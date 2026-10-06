"""Montagem dos casos de uso a partir das portas (um único lugar para a composição)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.coleta import ColetarCiclo, ReconciliarDia, SincronizarAgendas
from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.application.consolidacao import ConsolidarDia
from relatorio.application.envio import FINANCEIRO, SESMT, EnviarRelatorio, VerificarEnvio
from relatorio.application.exames import ComplementoExames, GeradorPlanilha, ProcessarExames
from relatorio.application.financeiro import ConsolidarFinanceiro
from relatorio.application.manutencao import (
    AlertaFalhasColeta,
    CompactarExecucoes,
    LimparRetencao,
    ReprocessarData,
)
from relatorio.application.metricas import ObterMetricasPeriodo
from relatorio.application.monitor import ObterMonitor
from relatorio.application.ports import (
    EnviadorEmail,
    ExamesGateway,
    FabricaUoW,
    FinanceiroGateway,
    Relogio,
    RenderizadorEmail,
    SesmtGateway,
    SggGateway,
)
from relatorio.application.sesmt import ConsolidarSesmt


@dataclass(frozen=True, slots=True)
class CasosDeUso:
    alertar: AlertarTecnico
    coletar: ColetarCiclo
    reconciliar: ReconciliarDia
    sincronizar: SincronizarAgendas
    metricas: ObterMetricasPeriodo
    monitor: ObterMonitor
    consolidar: ConsolidarDia
    enviar: EnviarRelatorio
    verificar_envio: VerificarEnvio
    reprocessar: ReprocessarData
    limpar: LimparRetencao
    compactar: CompactarExecucoes
    alerta_coleta: AlertaFalhasColeta
    consolidar_financeiro: ConsolidarFinanceiro | None = None
    enviar_financeiro: EnviarRelatorio | None = None
    verificar_financeiro: VerificarEnvio | None = None
    consolidar_sesmt: ConsolidarSesmt | None = None
    enviar_sesmt: EnviarRelatorio | None = None
    verificar_sesmt: VerificarEnvio | None = None
    processar_exames: ProcessarExames | None = None
    complemento_exames: ComplementoExames | None = None


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
    financeiro: FinanceiroGateway | None = None,
    sesmt: SesmtGateway | None = None,
    exames: ExamesGateway | None = None,
    planilha: GeradorPlanilha | None = None,
) -> CasosDeUso:
    alertar = AlertarTecnico(uow, email, renderizador, configuracao_padrao)
    reconciliar = ReconciliarDia(sgg, uow, relogio)
    metricas = ObterMetricasPeriodo(uow, relogio, configuracao_padrao, janela, coletor_habilitado)
    consolidar = ConsolidarDia(uow, relogio, metricas, sgg, alertar)
    processar_exames = (
        ProcessarExames(exames, uow, relogio, alertar) if exames is not None else None
    )
    complemento_exames = (
        ComplementoExames(processar_exames, exames, uow, planilha)
        if processar_exames is not None and exames is not None and planilha is not None
        else None
    )
    enviar = EnviarRelatorio(
        uow,
        relogio,
        consolidar,
        renderizador,
        email,
        alertar,
        destinatarios_override,
        complemento=complemento_exames,
    )
    consolidar_financeiro = (
        ConsolidarFinanceiro(financeiro, uow, relogio, alertar) if financeiro is not None else None
    )
    consolidar_sesmt = ConsolidarSesmt(sesmt, uow, relogio, alertar) if sesmt is not None else None
    return CasosDeUso(
        alertar=alertar,
        coletar=ColetarCiclo(sgg, uow, relogio),
        reconciliar=reconciliar,
        sincronizar=SincronizarAgendas(sgg, uow),
        metricas=metricas,
        monitor=ObterMonitor(metricas, relogio, janela),
        consolidar=consolidar,
        enviar=enviar,
        verificar_envio=VerificarEnvio(uow, alertar),
        reprocessar=ReprocessarData(consolidar, enviar, reconciliar),
        limpar=LimparRetencao(uow, relogio),
        compactar=CompactarExecucoes(uow, relogio),
        alerta_coleta=AlertaFalhasColeta(uow, alertar, janela.intervalo),
        consolidar_financeiro=consolidar_financeiro,
        enviar_financeiro=(
            EnviarRelatorio(
                uow,
                relogio,
                consolidar_financeiro,
                renderizador,
                email,
                alertar,
                destinatarios_override,
                tipo=FINANCEIRO,
            )
            if consolidar_financeiro is not None
            else None
        ),
        verificar_financeiro=VerificarEnvio(uow, alertar, FINANCEIRO),
        consolidar_sesmt=consolidar_sesmt,
        enviar_sesmt=(
            EnviarRelatorio(
                uow,
                relogio,
                consolidar_sesmt,
                renderizador,
                email,
                alertar,
                destinatarios_override,
                tipo=SESMT,
            )
            if consolidar_sesmt is not None
            else None
        ),
        verificar_sesmt=VerificarEnvio(uow, alertar, SESMT),
        processar_exames=processar_exames,
        complemento_exames=complemento_exames,
    )
