"""Dependências compartilhadas pelas rotas (contexto, login, CSRF e renderização)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from relatorio.application.configuracao import ConfiguracaoRelatorio
from relatorio.application.modelos import ExecucaoJob, StatusJob
from relatorio.config import Settings
from relatorio.domain.acesso import (
    ATENDIMENTO,
    CONFIGURACOES,
    EXECUCOES,
    FINANCEIRO,
    SESMT,
    TODOS_OS_MODULOS,
    Acesso,
)
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.seguranca import (
    CHAVE_PERFIL,
    CHAVE_USUARIO,
    PERFIL_ADMIN,
    ControleTentativas,
    NaoAutenticado,
    SemPermissao,
    token_csrf,
    validar_csrf,
)

URL_MODULO = {
    ATENDIMENTO: "/admin/atendimento",
    FINANCEIRO: "/admin/financeiro",
    SESMT: "/admin/sesmt",
    CONFIGURACOES: "/admin/configuracoes",
    EXECUCOES: "/admin/execucoes",
}
# Página (variável ``pagina`` dos templates) → módulo que fica marcado no menu.
PAGINA_MODULO = {
    "atendimento": ATENDIMENTO,
    "monitor": ATENDIMENTO,
    "agendas": ATENDIMENTO,
    "financeiro": FINANCEIRO,
    "sesmt": SESMT,
    "configuracoes": CONFIGURACOES,
    "usuarios": CONFIGURACOES,
    "execucoes": EXECUCOES,
}


@dataclass
class ContextoWeb:
    settings: Settings
    container: Container
    templates: Jinja2Templates
    tentativas: ControleTentativas

    def render(
        self, request: Request, nome: str, contexto: dict[str, Any], status_code: int = 200
    ) -> HTMLResponse:
        acesso = self.acesso(request)
        base = {
            "usuario": acesso.nome if acesso else None,
            "acesso": acesso,
            "menu": [
                {"chave": m.chave, "rotulo": m.rotulo, "url": URL_MODULO[m.chave]}
                for m in (acesso.permitidos() if acesso else ())
            ],
            "modulo_ativo": PAGINA_MODULO.get(str(contexto.get("pagina"))),
            "csrf": token_csrf(request),
            "mensagens": request.session.pop("mensagens", []),
            "ambiente": self.settings.app_env,
        }
        return self.templates.TemplateResponse(
            request, nome, {**base, **contexto}, status_code=status_code
        )

    def acesso(self, request: Request) -> Acesso | None:
        """Quem está logado, com as permissões de agora (lidas do banco a cada requisição,
        para que editar ou excluir um usuário valha na hora)."""
        login = request.session.get(CHAVE_USUARIO)
        if not login:
            return None
        em_cache: Acesso | None = getattr(request.state, "acesso", None)
        if em_cache is not None:
            return em_cache
        acesso = self.carregar_acesso(str(login), request.session.get(CHAVE_PERFIL) == PERFIL_ADMIN)
        if acesso is not None:
            request.state.acesso = acesso
        return acesso

    def carregar_acesso(self, login: str, administrador: bool) -> Acesso | None:
        if administrador:
            if login != self.settings.admin_user:  # ADMIN_USER mudou desde o login
                return None
            return Acesso(login, login, TODOS_OS_MODULOS, administrador=True)
        with self.container.uow() as uow:
            cadastrado = uow.usuarios.obter_por_usuario(login)
        if cadastrado is None:
            return None
        return Acesso(cadastrado.usuario, cadastrado.nome, frozenset(cadastrado.permissoes))

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


def exigir_acesso(request: Request) -> Acesso:
    acesso = obter_contexto(request).acesso(request)
    if acesso is None:  # sem sessão, ou usuário excluído depois do login
        request.session.clear()
        raise NaoAutenticado
    return acesso


def exigir_modulo(modulo: str) -> Callable[[Request], Acesso]:
    def dependencia(request: Request) -> Acesso:
        acesso = exigir_acesso(request)
        if not acesso.pode(modulo):
            raise SemPermissao(modulo)
        return acesso

    return dependencia


def exigir_csrf(request: Request, csrf: Annotated[str, Form()] = "") -> None:
    validar_csrf(request, csrf)


Ctx = Annotated[ContextoWeb, Depends(obter_contexto)]
Usuario = Annotated[Acesso, Depends(exigir_acesso)]  # logado, de qualquer módulo
UsuarioAtendimento = Annotated[Acesso, Depends(exigir_modulo(ATENDIMENTO))]
UsuarioFinanceiro = Annotated[Acesso, Depends(exigir_modulo(FINANCEIRO))]
UsuarioSesmt = Annotated[Acesso, Depends(exigir_modulo(SESMT))]
UsuarioConfiguracoes = Annotated[Acesso, Depends(exigir_modulo(CONFIGURACOES))]
UsuarioExecucoes = Annotated[Acesso, Depends(exigir_modulo(EXECUCOES))]
Csrf = Annotated[None, Depends(exigir_csrf)]


def mensagem(request: Request, texto: str, tipo: str = "ok") -> None:
    """Mensagem exibida uma vez na próxima página (flash)."""
    request.session.setdefault("mensagens", []).append({"texto": texto, "tipo": tipo})


def redirecionar(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
