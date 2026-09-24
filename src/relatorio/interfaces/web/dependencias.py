"""Dependências compartilhadas pelas rotas (contexto, login, CSRF e renderização)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from relatorio.application.configuracao import ConfiguracaoRelatorio
from relatorio.application.modelos import ExecucaoJob, StatusJob
from relatorio.config import Settings
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.seguranca import (
    CHAVE_USUARIO,
    ControleTentativas,
    NaoAutenticado,
    token_csrf,
    validar_csrf,
)


@dataclass
class ContextoWeb:
    settings: Settings
    container: Container
    templates: Jinja2Templates
    tentativas: ControleTentativas

    def render(
        self, request: Request, nome: str, contexto: dict[str, Any], status_code: int = 200
    ) -> HTMLResponse:
        base = {
            "usuario": request.session.get(CHAVE_USUARIO),
            "csrf": token_csrf(request),
            "mensagens": request.session.pop("mensagens", []),
            "ambiente": self.settings.app_env,
        }
        return self.templates.TemplateResponse(
            request, nome, {**base, **contexto}, status_code=status_code
        )

    def configuracao_atual(self) -> ConfiguracaoRelatorio:
        with self.container.uow() as uow:
            return self.settings.configuracao_padrao.mesclar(uow.configuracoes.obter_todas())

    def hoje(self) -> date:
        return self.container.relogio.agora().astimezone(FUSO_BRASILIA).date()


ATRASO_MAXIMO_COLETA = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SituacaoColeta:
    ultima: ExecucaoJob | None
    em_coleta: bool
    atrasada: bool
    agora: datetime


def situacao_coleta(ctx: ContextoWeb) -> SituacaoColeta:
    """Atrasada = em horário de coleta e sem ciclo bem-sucedido há mais de 5 min."""
    agora = ctx.container.relogio.agora()
    janela = ctx.settings.janela_coleta
    with ctx.container.uow() as uow:
        ultima = uow.execucoes.ultima("coletar_ciclo", StatusJob.SUCESSO)
    em_coleta = ctx.settings.coletor_habilitado and janela.contem(agora)
    inicio_janela = janela.do_dia(agora.astimezone(FUSO_BRASILIA).date())[0]
    referencia = max(ultima.inicio, inicio_janela) if ultima else inicio_janela
    atrasada = em_coleta and agora - referencia > ATRASO_MAXIMO_COLETA
    return SituacaoColeta(ultima=ultima, em_coleta=em_coleta, atrasada=atrasada, agora=agora)


def obter_contexto(request: Request) -> ContextoWeb:
    contexto: ContextoWeb = request.app.state.contexto
    return contexto


def exigir_login(request: Request) -> str:
    usuario = request.session.get(CHAVE_USUARIO)
    if not usuario:
        raise NaoAutenticado
    return str(usuario)


def exigir_csrf(request: Request, csrf: Annotated[str, Form()] = "") -> None:
    validar_csrf(request, csrf)


Ctx = Annotated[ContextoWeb, Depends(obter_contexto)]
Usuario = Annotated[str, Depends(exigir_login)]
Csrf = Annotated[None, Depends(exigir_csrf)]


def mensagem(request: Request, texto: str, tipo: str = "ok") -> None:
    """Mensagem exibida uma vez na próxima página (flash)."""
    request.session.setdefault("mensagens", []).append({"texto": texto, "tipo": tipo})


def redirecionar(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
