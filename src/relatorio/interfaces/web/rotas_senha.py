"""Telas do "Esqueci minha senha" e do link de definição de senha (ADR 0019).

A regra do token (geração, hash, validade, uso único) está em
``application/recuperacao_senha.py``; aqui ficam só as rotas e o endereço do link. O mesmo
link serve ao "Enviar acesso" da tela de usuários, com validade maior.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from urllib.parse import urljoin

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from relatorio.application.modelos import UsuarioSistema
from relatorio.application.ports import ErroIntegracao
from relatorio.application.recuperacao_senha import VALIDADE_ESQUECI, texto_validade
from relatorio.domain.acesso import SENHA_MIN, email_valido, validar_senha
from relatorio.interfaces.web.dependencias import (
    ContextoWeb,
    Csrf,
    Ctx,
    mensagem,
    redirecionar,
)
from relatorio.interfaces.web.seguranca import gerar_hash_senha

log = structlog.get_logger(__name__)
router = APIRouter(include_in_schema=False)


def enviar_link(
    ctx: ContextoWeb,
    request: Request,
    usuario: UsuarioSistema,
    validade: timedelta,
    *,
    convite: bool,
) -> None:
    """Manda o link ao e-mail do usuário (lança o erro do envio, se houver)."""
    # ADMIN_URL vale mais que o Host da requisição (que o cliente controla).
    base = ctx.settings.admin_url or str(request.base_url)
    ctx.container.recuperacao_senha.enviar_link(
        usuario, lambda token: urljoin(base, f"/redefinir-senha/{token}"), validade, convite=convite
    )


def _pagina_esqueci(
    request: Request,
    ctx: ContextoWeb,
    *,
    email: str = "",
    erro: str | None = None,
    enviado: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    contexto = {
        "email": email,
        "erro": erro,
        "enviado": enviado,
        "validade": texto_validade(VALIDADE_ESQUECI),
    }
    return ctx.render(request, "admin/esqueci_senha.html", contexto, status_code=status_code)


@router.get("/esqueci-senha", response_class=HTMLResponse)
def esqueci(request: Request, ctx: Ctx) -> HTMLResponse:
    return _pagina_esqueci(request, ctx)


@router.post("/esqueci-senha", response_class=HTMLResponse)
def pedir_link(
    request: Request, ctx: Ctx, _csrf: Csrf, email: Annotated[str, Form()] = ""
) -> HTMLResponse:
    ip = request.client.host if request.client else "?"
    email = email.strip().lower()
    # Dois limites (5 a cada 15 min, como o login). Por IP: só e-mail inválido ou sem perfil
    # conta, então vários colegas atrás do mesmo IP pedem o seu link sem se atrapalhar. Por
    # e-mail: cada envio conta, para ninguém lotar a caixa de outra pessoa.
    chave_ip = f"esqueci|{ip}"
    if ctx.tentativas.bloqueado(chave_ip):
        log.warning("esqueci_senha_bloqueado", ip=ip)
        erro = "Muitas tentativas. Aguarde 15 minutos e tente de novo."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=429)
    if not email_valido(email):
        ctx.tentativas.registrar_falha(chave_ip)
        erro = "Informe um e-mail válido."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=422)
    with ctx.container.uow() as uow:
        usuario = uow.usuarios.obter_por_email(email)
    if usuario is None:
        ctx.tentativas.registrar_falha(chave_ip)
        # Pedido do negócio: dizer que o e-mail não tem perfil (revela quem tem; ADR 0019).
        erro = "Não existe perfil cadastrado com esse e-mail."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=404)
    chave_email = f"esqueci-email|{email}"  # só e-mails com perfil: a memória não cresce à toa
    if ctx.tentativas.bloqueado(chave_email):
        erro = "Já enviamos vários links para esse e-mail. Aguarde 15 minutos e tente de novo."
        return _pagina_esqueci(request, ctx, email=email, erro=erro, status_code=429)
    ctx.tentativas.registrar_falha(chave_email)
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


@router.get("/redefinir-senha/{token}", response_class=HTMLResponse)
def pagina_redefinir(token: str, request: Request, ctx: Ctx) -> HTMLResponse:
    return _pagina_redefinir(
        request, ctx, token, ctx.container.recuperacao_senha.dono_do_link(token)
    )


@router.post("/redefinir-senha/{token}", response_class=HTMLResponse, response_model=None)
def redefinir(
    token: str,
    request: Request,
    ctx: Ctx,
    _csrf: Csrf,
    senha: Annotated[str, Form()] = "",
    confirmacao: Annotated[str, Form()] = "",
) -> Response:
    recuperacao = ctx.container.recuperacao_senha
    usuario = recuperacao.dono_do_link(token)
    if usuario is None:
        return _pagina_redefinir(request, ctx, token, None)
    try:
        senha_ok = validar_senha(senha)
        if senha != confirmacao:
            raise ValueError("A confirmação não confere com a senha.")
    except ValueError as erro:
        return _pagina_redefinir(request, ctx, token, usuario, str(erro), status_code=422)
    if not recuperacao.redefinir(token, gerar_hash_senha(senha_ok)):
        return _pagina_redefinir(request, ctx, token, None)  # usado ou vencido ao digitar
    log.info("senha_redefinida", usuario=usuario.usuario)
    mensagem(request, "Senha definida. Entre com o seu usuário e a nova senha.")
    return redirecionar("/login")
