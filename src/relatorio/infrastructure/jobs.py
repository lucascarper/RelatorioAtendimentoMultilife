"""Execução de jobs com trava distribuída e auditoria (``execucao_job``).

* Advisory lock do PostgreSQL: dois containers (ex.: durante um deploy) nunca rodam o
  mesmo job ao mesmo tempo — quem não conseguir a trava registra "ignorado" no log.
* Cada execução grava início, fim, status, duração e detalhes (RNF08).
"""

from __future__ import annotations

import time as time_module
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime

import structlog
from sqlalchemy import Engine, text

from relatorio.application.modelos import StatusJob
from relatorio.application.ports import ErroIntegracao, FabricaUoW, Relogio

log = structlog.get_logger(__name__)


class ExecutorJobs:
    def __init__(self, engine: Engine | None, uow: FabricaUoW, relogio: Relogio) -> None:
        """``engine=None`` dispensa a trava (demonstração em memória, processo único)."""
        self._engine = engine
        self._uow = uow
        self._relogio = relogio

    def executar(
        self,
        job: str,
        funcao: Callable[[], Mapping[str, object] | None],
        *,
        trava: str | None = None,
        ao_falhar: Callable[[], object] | None = None,
    ) -> StatusJob:
        chave = trava or job
        logger = log.bind(job=job, ciclo_id=uuid.uuid4().hex[:12])
        if self._engine is None:
            return self._executar_com_trava(job, funcao, logger, ao_falhar)
        with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conexao:
            obteve = conexao.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:chave))"), {"chave": chave}
            ).scalar()
            if not obteve:
                logger.info("job_ignorado_trava_ocupada", trava=chave)
                return StatusJob.IGNORADO
            try:
                return self._executar_com_trava(job, funcao, logger, ao_falhar)
            finally:
                conexao.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:chave))"), {"chave": chave}
                )

    def _executar_com_trava(
        self,
        job: str,
        funcao: Callable[[], Mapping[str, object] | None],
        logger: structlog.stdlib.BoundLogger,
        ao_falhar: Callable[[], object] | None,
    ) -> StatusJob:
        inicio: datetime = self._relogio.agora()
        cronometro = time_module.perf_counter()
        with self._uow() as uow:
            id_execucao = uow.execucoes.iniciar(job, inicio, {})
            uow.commit()

        def duracao_ms() -> int:
            return round((time_module.perf_counter() - cronometro) * 1000)

        try:
            detalhe = dict(funcao() or {})
        except Exception as erro:
            falha = {"erro": f"{type(erro).__name__}: {erro}", "duracao_ms": duracao_ms()}
            with self._uow() as uow:
                uow.execucoes.finalizar(id_execucao, self._relogio.agora(), StatusJob.FALHA, falha)
                uow.commit()
            if isinstance(erro, ErroIntegracao):
                logger.warning("job_falhou", **falha)  # SGG/SMTP fora: esperado, sem traceback
            else:
                logger.exception("job_falhou", **falha)
            if ao_falhar is not None:
                try:
                    ao_falhar()
                except Exception:
                    logger.exception("job_ao_falhar_falhou")
            return StatusJob.FALHA

        detalhe["duracao_ms"] = duracao_ms()
        with self._uow() as uow:
            uow.execucoes.finalizar(id_execucao, self._relogio.agora(), StatusJob.SUCESSO, detalhe)
            uow.commit()
        logger.info("job_concluido", **detalhe)
        return StatusJob.SUCESSO
