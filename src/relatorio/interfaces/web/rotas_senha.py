"""Esqueci minha senha e o link de definição de senha (ADR 0019).

O link leva um token aleatório de uso único; o banco guarda só o SHA-256 dele. O mesmo
link serve ao "Enviar acesso" da tela de usuários, com validade maior.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import urljoin

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from relatorio.application.modelos import UsuarioSistema
from relatorio.application.ports import ErroIntegracao
from relatorio.domain.acesso import SENHA_MIN, validar_senha
from relatorio.interfaces.web.dependencias import (
    ContextoWeb,
    Csrf,
    Ctx,
    mensagem,
    redirecionar,
)
from relatorio.interfaces.web.rotas_admin import EMAIL_VALIDO
from relatorio.interfaces.web.seguranca import gerar_hash_senha

log = structlog.get_logger(__name__)
router = APIRouter(include_in_schema=False)

VALIDADE_ESQUECI = timedelta(hours=1)
VALIDADE_CONVITE = timedelta(hours=24)  # quem recebe o acesso pode demorar a ver o e-mail


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _texto_validade(validade: timedelta) -> str:
    horas = int(validade.total_seconds() // 3600)
    return "1 hora" if horas == 1 else f"{horas} horas"


def enviar_link(
    ctx: ContextoWeb,
    request: Request,
    usuario: UsuarioSistema,
    validade: timedelta,
    *,
    convite: bool,
) -> None:
    """Cria o link de uso único e manda ao e-mail do usuário (lança ``ErroIntegracao``)."""
    if not usuario.email:
        raise ValueError("usuário sem e-mail")
    token = secrets.token_urlsafe(32)
    with ctx.container.uow() as uow:
        expira = ctx.container.relogio.agora() + validade
        uow.tokens_senha.criar(_hash_token(token), usuario.id, expira)
        uow.commit()
    # ADMIN_URL vale mais que o Host da requisição (que o cliente controla).
    link = urljoin(ctx.settings.admin_url or str(request.base_url), f"/redefinir-senha/{token}")
    conteudo = ctx.container.renderizador.acesso(
        usuario.nome, usuario.usuario, link, _texto_validade(validade), convite=convite
    )
    ctx.container.email.enviar([usuario.email], conteudo)


def _pagina_esqueci(
    request: Request,
    ctx: ContextoWeb,
    *,
    email: str = "",
    erro: str | None = None,
    enviado: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    contexto = {"email": email, "erro": erro, "enviado": enviado, "validade": "1 hora"}
    return ctx.render(request, "admin/esqueci_senha.html", contexto, status_code=status_code)


@router.get("/esqueci-senha", response_class=HTMLResponse)
def esqueci(request: Request, ctx: Ctx) -> HTMLResponse:
    return _pagina_esqueci(request, ctx)


@router.post("/esqueci-senha", response_class=HTMLResponse)
def pedir_link(
    request: Request, ctx: Ctx, _csrf: Csrf, email: Annotated[str, Form()] = ""
) -> HTMLResponse:
    ip = request.client.host if request.client else "?"
    chave = f"esqueci|{ip}"
    email = email.strip().lower()
    if ctx.tentativas.bloqueado(chave):
        log.warning("esqueci_senha_bloqueado", ip=ip)
        erro = "Muitos pedidos seguidos. Aguarde 15 minutos e tente de novo."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=429)
    ctx.tentativas.registrar_falha(chave)  # todo pedido conta: 5 a cada 15 min por IP
    if not EMAIL_VALIDO.match(email) or len(email) > 254:
        erro = "Informe um e-mail válido."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=422)
    with ctx.container.uow() as uow:
        usuario = uow.usuarios.obter_por_email(email)
    if usuario is None:
        # Pedido do negócio: dizer que o e-mail não tem perfil (revela quem tem; ADR 0019).
        erro = "Não existe perfil cadastrado com esse e-mail."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=404)
    try:
        enviar_link(ctx, request, usuario, VALIDADE_ESQUECI, convite=False)
    except (ErroIntegracao, OSError) as falha:  # OSError: conexão SMTP recusada
        log.error("esqueci_senha_falha_email", usuario=usuario.usuario, erro=str(falha))
        erro = "Não foi possível enviar o e-mail agora. Tente de novo em alguns minutos."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=503)
    log.info("esqueci_senha_link_enviado", usuario=usuario.usuario, ip=ip)
    return _pagina_esqueci(request, ctx, email=email, enviado=True)


def _pagina_redefinir(
    request: Request,
    ctx: ContextoWeb,
    token: str,
    usuario: UsuarioSistema | None,
    erro: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return ctx.render(
        request,
        "admin/redefinir_senha.html",
        {"token": token, "alvo": usuario, "erro": erro, "senha_minima": SENHA_MIN},
        status_code=404 if usuario is None else status_code,
    )


def _dono_do_link(ctx: ContextoWeb, token: str) -> UsuarioSistema | None:
    with ctx.container.uow() as uow:
        id_usuario = uow.tokens_senha.valido(_hash_token(token), ctx.container.relogio.agora())
        return uow.usuarios.obter(id_usuario) if id_usuario is not None else None


@router.get("/redefinir-senha/{token}", response_class=HTMLResponse)
def pagina_redefinir(token: str, request: Request, ctx: Ctx) -> HTMLResponse:
    return _pagina_redefinir(request, ctx, token, _dono_do_link(ctx, token))


@router.post("/redefinir-senha/{token}", response_class=HTMLResponse, response_model=None)
def redefinir(
    token: str,
    request: Request,
    ctx: Ctx,
    _csrf: Csrf,
    senha: Annotated[str, Form()] = "",
    confirmacao: Annotated[str, Form()] = "",
) -> Response:
    usuario = _dono_do_link(ctx, token)
    if usuario is None:
        return _pagina_redefinir(request, ctx, token, None)
    try:
        senha_ok = validar_senha(senha)
        if senha != confirmacao:
            raise ValueError("A confirmação não confere com a senha.")
    except ValueError as erro:
        return _pagina_redefinir(request, ctx, token, usuario, str(erro), status_code=422)
    novo_hash = gerar_hash_senha(senha_ok)
    with ctx.container.uow() as uow:
        id_usuario = uow.tokens_senha.consumir(_hash_token(token), ctx.container.relogio.agora())
        if id_usuario is None:  # o link foi usado (ou venceu) enquanto a senha era digitada
            return _pagina_redefinir(request, ctx, token, None)
        uow.usuarios.definir_senha(id_usuario, novo_hash)
        uow.commit()
    ip = request.client.host if request.client else "?"
    ctx.tentativas.limpar(f"{ip}|{usuario.usuario}")  # libera o login, se estava bloqueado
    log.info("senha_redefinida", usuario=usuario.usuario, ip=ip)
    mensagem(request, "Senha definida. Entre com o seu usuário e a nova senha.")
    return redirecionar("/login")
