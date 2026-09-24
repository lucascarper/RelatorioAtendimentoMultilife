"""Admin web (RF07/RF08) e /health — FastAPI + Jinja2 + HTMX.

Início: ``uvicorn relatorio.interfaces.web.app:app --host 0.0.0.0 --port $PORT``
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from relatorio.config import Settings, obter_settings
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.resumo import dia_semana
from relatorio.infrastructure.container import Container
from relatorio.infrastructure.email import apresentacao as fmt
from relatorio.infrastructure.logs import configurar_logs
from relatorio.interfaces.web import rotas_admin, rotas_publicas
from relatorio.interfaces.web.dependencias import ContextoWeb
from relatorio.interfaces.web.seguranca import ControleTentativas, NaoAutenticado

log = structlog.get_logger(__name__)

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'self'; form-action 'self'"
)


def _data_br(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else "—"


def _hora_br(valor: datetime | None) -> str:
    return valor.astimezone(FUSO_BRASILIA).strftime("%H:%M") if valor else "—"


def _datahora_br(valor: datetime | None) -> str:
    return valor.astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M:%S") if valor else "—"


def criar_templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(settings.templates_dir))
    templates.env.filters.update(
        {
            "duracao": fmt.duracao,
            "numero": fmt.numero,
            "percentual": fmt.percentual,
            "data_br": _data_br,
            "hora_br": _hora_br,
            "datahora_br": _datahora_br,
            "dia_semana": dia_semana,
        }
    )
    return templates


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or obter_settings()
    contexto = ContextoWeb(
        settings=settings,
        container=container or Container(settings),
        templates=criar_templates(settings),
        tentativas=ControleTentativas(),
    )

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
        configurar_logs(settings.log_level)
        pendencias = settings.pendencias("web")
        if pendencias:
            log.error("configuracao_incompleta", faltando=pendencias)
        yield
        contexto.container.fechar()

    app = FastAPI(
        title="Relatório Diário de Atendimentos — MultiLife",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=ciclo_de_vida,
    )
    app.state.contexto = contexto
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value() or secrets.token_urlsafe(32),
        session_cookie="relatorio_sessao",
        max_age=8 * 3600,
        same_site="lax",
        https_only=settings.app_env == "production",
    )

    @app.middleware("http")
    async def cabecalhos_seguranca(
        request: Request, proximo: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resposta = await proximo(request)
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        resposta.headers.setdefault("Content-Security-Policy", CSP)
        if settings.app_env == "production":
            resposta.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return resposta

    @app.exception_handler(NaoAutenticado)
    async def redirecionar_login(request: Request, _erro: NaoAutenticado) -> Response:
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    app.include_router(rotas_publicas.router)
    app.include_router(rotas_admin.router)
    return app


def __getattr__(nome: str) -> Any:
    """``app`` é criado sob demanda: importar o módulo nos testes não exige variáveis."""
    if nome == "app":
        instancia = create_app()
        globals()["app"] = instancia
        return instancia
    raise AttributeError(nome)
