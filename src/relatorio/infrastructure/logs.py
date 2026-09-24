"""Logs estruturados em JSON (RNF08), filtráveis nos logs da Railway."""

from __future__ import annotations

import logging
import sys

import structlog

_CAMPOS_SENSIVEIS = ("authorization", "senha", "password", "api_key", "sgg_api_key", "token")


def _mascarar_sensiveis(
    _logger: object, _metodo: str, evento: structlog.types.EventDict
) -> structlog.types.EventDict:
    for chave in list(evento):
        if any(s in chave.lower() for s in _CAMPOS_SENSIVEIS):
            evento[chave] = "***"
    return evento


def configurar_logs(nivel: str = "INFO", json: bool = True) -> None:
    processadores: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        _mascarar_sensiveis,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderizador: structlog.types.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*processadores, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=processadores,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderizador],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel.upper())
    # httpx registra cada URL em INFO; o APScheduler é verboso. Mantém só avisos.
    for barulhento in ("httpx", "httpcore", "apscheduler", "uvicorn.access"):
        logging.getLogger(barulhento).setLevel(logging.WARNING)
