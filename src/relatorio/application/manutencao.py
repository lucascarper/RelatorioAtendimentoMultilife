"""Reprocessamento (RF08), retenção (RNF07) e alerta de falhas de coleta."""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, time, timedelta

import structlog

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.coleta import ReconciliarDia
from relatorio.application.configuracao import inicio_do_dia
from relatorio.application.consolidacao import ConsolidarDia
from relatorio.application.envio import EnviarRelatorio
from relatorio.application.metricas import JOB_COLETA
from relatorio.application.ports import ErroIntegracao, FabricaUoW, Relogio
from relatorio.domain.entidades import FUSO_BRASILIA

RETENCAO_MESES = 24
# Alerta quando a coleta passa este tempo falhando sem parar (10 ciclos de 1 min).
TEMPO_FALHANDO_ALERTA = timedelta(minutes=10)

log = structlog.get_logger(__name__)


def subtrair_meses(dia: date, meses: int) -> date:
    total = dia.year * 12 + (dia.month - 1) - meses
    ano, mes = divmod(total, 12)
    mes += 1
    return date(ano, mes, min(dia.day, calendar.monthrange(ano, mes)[1]))


class ReprocessarData:
    """Recalcula (e opcionalmente reenvia) o relatório de qualquer data."""

    def __init__(
        self,
        consolidar: ConsolidarDia,
        enviar: EnviarRelatorio,
        reconciliar: ReconciliarDia | None,
    ) -> None:
        self._consolidar = consolidar
        self._enviar = enviar
        self._reconciliar = reconciliar

    def executar(
        self, dia: date, *, reconciliar: bool = False, enviar: bool = False
    ) -> dict[str, object]:
        detalhe: dict[str, object] = {"referencia": dia.isoformat()}
        if reconciliar and self._reconciliar is not None:
            try:
                detalhe["reconciliacao"] = self._reconciliar.executar(dia)
            except ErroIntegracao as erro:
                log.warning("reprocessar_sem_reconciliacao", referencia=dia.isoformat())
                detalhe["reconciliacao"] = f"indisponível: {erro}"
        detalhe["consolidacao"] = self._consolidar.executar(dia)
        if enviar:
            detalhe["envio"] = self._enviar.executar(dia, forcar=True)
        return detalhe


class LimparRetencao:
    def __init__(self, uow: FabricaUoW, relogio: Relogio) -> None:
        self._uow = uow
        self._relogio = relogio

    def executar(self) -> dict[str, object]:
        hoje = self._relogio.agora().astimezone(FUSO_BRASILIA).date()
        limite = subtrair_meses(hoje, RETENCAO_MESES)
        limite_instante = datetime.combine(limite, time.min, tzinfo=FUSO_BRASILIA)
        with self._uow() as uow:
            eventos = uow.eventos.apagar_anteriores_a(limite_instante)
            snapshots = uow.snapshots.apagar_anteriores_a(limite)
            resumos = uow.resumos.apagar_anteriores_a(limite)
            execucoes = uow.execucoes.apagar_anteriores_a(limite_instante)
            uow.commit()
        return {
            "referencia": f"{hoje:%Y-%m}",
            "limite": limite.isoformat(),
            "eventos": eventos,
            "snapshots": snapshots,
            "resumos": resumos,
            "execucoes": execucoes,
        }


class CompactarExecucoes:
    """Mantém um ciclo de coleta bem-sucedido por minuto nos dias anteriores.

    Com a coleta a cada poucos segundos, o histórico de execuções cresceria milhares de
    linhas por dia. Para detectar lacunas de coleta (tolerância de 5 min) basta um
    sucesso por minuto; as falhas e o dia de hoje ficam intactos.
    """

    def __init__(self, uow: FabricaUoW, relogio: Relogio) -> None:
        self._uow = uow
        self._relogio = relogio

    def executar(self) -> dict[str, object]:
        hoje = self._relogio.agora().astimezone(FUSO_BRASILIA).date()
        with self._uow() as uow:
            removidas = uow.execucoes.compactar_sucessos(JOB_COLETA, inicio_do_dia(hoje))
            uow.commit()
        return {"referencia": hoje.isoformat(), "execucoes": removidas}


class AlertaFalhasColeta:
    """Avisa o técnico quando a coleta falha por 10 min seguidos (uma vez por sequência)."""

    def __init__(
        self, uow: FabricaUoW, alertar: AlertarTecnico, intervalo: timedelta = timedelta(minutes=1)
    ) -> None:
        self._uow = uow
        self._alertar = alertar
        self._limite = max(1, math.ceil(TEMPO_FALHANDO_ALERTA / intervalo))

    def verificar(self) -> bool:
        with self._uow() as uow:
            falhas = uow.execucoes.falhas_consecutivas(JOB_COLETA)
            ultima = uow.execucoes.ultima(JOB_COLETA)
        if falhas != self._limite:
            return False
        erro = str(ultima.detalhe.get("erro", "")) if ultima else ""
        return self._alertar.enviar(
            "Coleta do SGG falhando",
            f"A coleta de agendamentos falhou {falhas} vezes seguidas. "
            "Os tempos do dia podem ficar incompletos.",
            {"Último erro": erro or "—"},
        )
