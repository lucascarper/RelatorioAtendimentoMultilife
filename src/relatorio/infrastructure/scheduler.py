"""Worker: registra e executa todos os jobs (seção 11). Fuso America/Sao_Paulo.

| Horário                  | Job                | Se falhar                              |
|--------------------------|--------------------|----------------------------------------|
| 05:30                    | sync_agendas       | usa o cadastro anterior                |
| 06:00–18:00, a cada 5 s  | coletar_ciclo      | próximo ciclo recupera pela sobreposição|
| 18:30                    | reconciliar_dia    | reexecuta às 19:00 e 22:00             |
| 23:00                    | consolidar_dia     | reexecuta às 02:00 e 05:00             |
| 07:59                    | enviar_relatorio   | 08:01 e 08:03; depois alerta técnico   |
| 08:10                    | verificar_envio    | alerta se não foi entregue             |
| dia 1, 03:00             | limpar_retencao    | reexecuta no dia seguinte              |

Todos com ``max_instances=1`` e ``coalesce=True``; o advisory lock do PostgreSQL impede
que dois containers (ex.: durante um deploy) rodem o mesmo job.

Início: ``python -m relatorio.infrastructure.scheduler``
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Callable, Mapping
from datetime import date
from types import FrameType
from typing import Any

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from relatorio.application.consolidacao import dia_para_consolidar
from relatorio.application.envio import dia_do_relatorio
from relatorio.config import Settings, obter_settings
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.container import Container
from relatorio.infrastructure.logs import configurar_logs

log = structlog.get_logger(__name__)

TRAVA_COLETOR = "coletor"  # polling e reconciliação nunca rodam ao mesmo tempo


class JobsAgendados:
    """Funções chamadas pelo APScheduler; cada uma passa pelo ExecutorJobs."""

    def __init__(self, container: Container) -> None:
        self._c = container
        self._casos = container.casos
        self._executor = container.executor
        self._janela = container.settings.janela_coleta

    def _hoje(self) -> date:
        return self._c.relogio.agora().astimezone(FUSO_BRASILIA).date()

    def _ja_executado(self, job: str, referencia: str) -> bool:
        with self._c.uow() as uow:
            return uow.execucoes.houve_sucesso(job, referencia)

    def sync_agendas(self) -> None:
        self._executor.executar("sync_agendas", self._casos.sincronizar.executar)

    def coletar_ciclo(self) -> None:
        if not self._janela.contem(self._c.relogio.agora()):
            return
        self._executor.executar(
            "coletar_ciclo",
            self._casos.coletar.executar,
            trava=TRAVA_COLETOR,
            ao_falhar=self._casos.alerta_coleta.verificar,
        )

    def reconciliar_dia(self, tentativa: int) -> None:
        dia = self._hoje()
        if tentativa > 1 and self._ja_executado("reconciliar_dia", dia.isoformat()):
            return
        self._executor.executar(
            "reconciliar_dia", lambda: self._casos.reconciliar.executar(dia), trava=TRAVA_COLETOR
        )

    def consolidar_dia(self, tentativa: int) -> None:
        dia = dia_para_consolidar(self._c.relogio.agora())
        self._executor.executar(
            "consolidar_dia", lambda: self._casos.consolidar.executar_agendado(dia, tentativa)
        )

    def enviar_relatorio(self, tentativa: int) -> None:
        dia = dia_do_relatorio(self._c.relogio.agora())
        self._executor.executar(
            "enviar_relatorio", lambda: self._casos.enviar.executar_agendado(dia, tentativa)
        )

    def verificar_envio(self) -> None:
        dia = dia_do_relatorio(self._c.relogio.agora())
        self._executor.executar(
            "verificar_envio", lambda: self._casos.verificar_envio.executar(dia)
        )

    def compactar_execucoes(self) -> None:
        self._executor.executar("compactar_execucoes", self._casos.compactar.executar)

    def limpar_retencao(self) -> None:
        mes = f"{self._hoje():%Y-%m}"
        if self._ja_executado("limpar_retencao", mes):
            return
        self._executor.executar("limpar_retencao", self._casos.limpar.executar)


def _cron(**campos: Any) -> CronTrigger:
    return CronTrigger(timezone=FUSO_BRASILIA, **campos)


def _segundos_do_ciclo(intervalo_s: int) -> str:
    """Segundos do minuto em que a coleta roda: ``0`` (1×/min) ou ``*/5`` (a cada 5 s)."""
    return "0" if intervalo_s >= 60 else f"*/{intervalo_s}"


def registrar_jobs(agendador: BlockingScheduler, jobs: JobsAgendados, settings: Settings) -> None:
    def adicionar(
        id_job: str,
        funcao: Callable[..., None],
        gatilho: CronTrigger,
        kwargs: Mapping[str, Any] | None = None,
        tolerancia_s: int = 300,
    ) -> None:
        agendador.add_job(
            funcao,
            gatilho,
            id=id_job,
            kwargs=dict(kwargs or {}),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=tolerancia_s,
            replace_existing=True,
        )

    adicionar("sync_agendas", jobs.sync_agendas, _cron(hour=5, minute=30))

    if settings.coletor_habilitado:
        janela = settings.janela_coleta
        adicionar(
            "coletar_ciclo",
            jobs.coletar_ciclo,
            _cron(
                hour=f"{janela.inicio.hour}-{janela.fim.hour}",
                minute="*",
                second=_segundos_do_ciclo(settings.coleta_intervalo_s),
            ),
            tolerancia_s=min(30, settings.coleta_intervalo_s),
        )
        for tentativa, (hora, minuto) in enumerate(((18, 30), (19, 0), (22, 0)), start=1):
            adicionar(
                f"reconciliar_dia_{tentativa}",
                jobs.reconciliar_dia,
                _cron(hour=hora, minute=minuto),
                {"tentativa": tentativa},
            )
    else:
        log.info("coletor_desabilitado", motivo="eventos gravados pelo coletor do painel")

    for tentativa, hora in enumerate((23, 2, 5), start=1):
        adicionar(
            f"consolidar_dia_{tentativa}",
            jobs.consolidar_dia,
            _cron(hour=hora, minute=0),
            {"tentativa": tentativa},
        )
    for tentativa, (hora, minuto) in enumerate(((7, 59), (8, 1), (8, 3)), start=1):
        adicionar(
            f"enviar_relatorio_{tentativa}",
            jobs.enviar_relatorio,
            _cron(hour=hora, minute=minuto),
            {"tentativa": tentativa},
            tolerancia_s=120,
        )
    adicionar("verificar_envio", jobs.verificar_envio, _cron(hour=8, minute=10))
    adicionar("limpar_retencao", jobs.limpar_retencao, _cron(day="1-2", hour=3, minute=0))
    adicionar("compactar_execucoes", jobs.compactar_execucoes, _cron(hour=3, minute=20))


def main() -> None:
    settings = obter_settings()
    configurar_logs(settings.log_level)
    pendencias = settings.pendencias("worker")
    if pendencias:
        log.error("configuracao_incompleta", faltando=pendencias)
        if settings.app_env == "production":
            sys.exit(1)

    container = Container(settings)
    agendador = BlockingScheduler(timezone=FUSO_BRASILIA)
    registrar_jobs(agendador, JobsAgendados(container), settings)

    def encerrar(_sinal: int, _frame: FrameType | None) -> None:
        log.info("worker_encerrando")
        agendador.shutdown(wait=True)

    signal.signal(signal.SIGTERM, encerrar)
    signal.signal(signal.SIGINT, encerrar)
    log.info(
        "worker_iniciado",
        ambiente=settings.app_env,
        jobs=[job.id for job in agendador.get_jobs()],
    )
    try:
        agendador.start()
    finally:
        container.fechar()


if __name__ == "__main__":
    main()
