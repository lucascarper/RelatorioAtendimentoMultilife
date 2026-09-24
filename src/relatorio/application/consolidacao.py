"""RF04/RF11 — consolidação do dia em ``resumo_diario``."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import structlog

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.coleta import registrar_observacoes
from relatorio.application.configuracao import fim_do_dia, inicio_do_dia
from relatorio.application.metricas import ObterMetricasPeriodo
from relatorio.application.ports import ErroIntegracao, FabricaUoW, Relogio, SggGateway
from relatorio.domain.comparativo import comparar
from relatorio.domain.entidades import FUSO_BRASILIA, OrigemEvento, Situacao
from relatorio.domain.metricas import VERSAO_REGRA, Kpis
from relatorio.domain.resumo import ResumoDiario

JOB_CONSOLIDAR = "consolidar_dia"
HORA_CONSOLIDACAO = 23

log = structlog.get_logger(__name__)


def dia_para_consolidar(agora: datetime) -> date:
    """Às 23:00 consolida o próprio dia; nas retentativas (02:00, 05:00), o dia anterior."""
    local = agora.astimezone(FUSO_BRASILIA)
    if local.hour >= HORA_CONSOLIDACAO:
        return local.date()
    return local.date() - timedelta(days=1)


class ConsolidarDia:
    def __init__(
        self,
        uow: FabricaUoW,
        relogio: Relogio,
        obter_metricas: ObterMetricasPeriodo,
        sgg: SggGateway | None,
        alertar: AlertarTecnico,
    ) -> None:
        self._uow = uow
        self._relogio = relogio
        self._obter_metricas = obter_metricas
        self._sgg = sgg
        self._alertar = alertar

    def executar(self, dia: date) -> dict[str, object]:
        faltas, verificacao_indisponivel = self._conferir_faltas(dia)
        resultado = self._obter_metricas.executar(
            inicio_do_dia(dia),
            fim_do_dia(dia),
            faltas_confirmadas=faltas,
            verificacao_faltas_indisponivel=verificacao_indisponivel,
        )
        data_base = dia - timedelta(days=7)
        with self._uow() as uow:
            anterior = uow.resumos.obter(data_base)
        kpis_anteriores = (
            Kpis.de_dict(anterior.metricas.get("kpis", {}))
            if anterior is not None and not anterior.metricas.get("sem_movimento", False)
            else None
        )
        agora = self._relogio.agora()
        resumo = ResumoDiario(
            data_referencia=dia,
            unidades=resultado.unidades,
            gerado_em=agora,
            metricas=resultado.metricas,
            comparativo=comparar(resultado.metricas.kpis, kpis_anteriores, data_base),
        )
        with self._uow() as uow:
            uow.resumos.salvar_metricas(dia, resumo.para_json(), VERSAO_REGRA, agora)
            uow.commit()
        kpis = resultado.metricas.kpis
        return {
            "referencia": dia.isoformat(),
            "agendados": kpis.agendados,
            "atendimentos": kpis.atendimentos,
            "faltas": kpis.faltas,
            "sem_movimento": resumo.sem_movimento,
            "faltas_confirmadas_na_agenda": len(faltas),
            "verificacao_faltas_indisponivel": verificacao_indisponivel,
        }

    def executar_agendado(self, dia: date, tentativa: int, total: int = 3) -> dict[str, object]:
        """Execução pelo agendador: 23:00, 02:00 e 05:00. Falhou na última? Alerta (RF10)."""
        if tentativa > 1:
            with self._uow() as uow:
                if uow.execucoes.houve_sucesso(JOB_CONSOLIDAR, dia.isoformat()):
                    return {"referencia": dia.isoformat(), "status": "ja_consolidado"}
        try:
            return self.executar(dia)
        except Exception as erro:
            if tentativa >= total:
                self._alertar.enviar(
                    "Consolidação do dia falhou",
                    f"A consolidação de {dia:%d/%m/%Y} falhou nas {total} tentativas. "
                    "O relatório não será enviado incompleto (RF10).",
                    {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
            raise

    def _conferir_faltas(self, dia: date) -> tuple[set[int], bool]:
        """RF11 + conferência final do dia, antes de fechar os números.

        Relê o dia no SGG para os agendamentos ainda pendentes (Agendado, Aguardando ou
        Em Atendimento). Quem estava sem baixa e aparece como "Faltou" conta como falta
        (falta registrada na agenda do consultório); finalizações feitas depois da
        reconciliação das 18:30 também entram no log de eventos.
        """
        with self._uow() as uow:
            snapshots = uow.snapshots.listar_por_data(dia, dia)
        sem_baixa = {
            s.id_agendamento
            for s in snapshots
            if s.situacao_atual in (Situacao.AGENDADO, Situacao.AGUARDANDO)
        }
        pendentes = sem_baixa | {
            s.id_agendamento for s in snapshots if s.situacao_atual is Situacao.EM_ATENDIMENTO
        }
        if not pendentes or self._sgg is None:
            return set(), False
        try:
            registros = self._sgg.agendamentos_do_dia(dia)
        except ErroIntegracao as erro:
            log.warning(
                "verificacao_faltas_indisponivel", referencia=dia.isoformat(), erro=str(erro)
            )
            return set(), True
        atualizados = [r for r in registros if r.id_agendamento in pendentes]
        faltas = [
            r
            for r in atualizados
            if r.situacao is Situacao.FALTOU and r.id_agendamento in sem_baixa
        ]
        outros = [r for r in atualizados if r not in faltas]
        agora = self._relogio.agora()
        with self._uow() as uow:
            registrar_observacoes(uow, faltas, agora, OrigemEvento.VERIFICACAO_FALTA)
            registrar_observacoes(uow, outros, agora, OrigemEvento.RECONCILIACAO)
            uow.commit()
        return {r.id_agendamento for r in faltas}, False
