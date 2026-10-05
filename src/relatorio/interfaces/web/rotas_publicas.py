"""Rotas sem login: /health, /login e /logout."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from relatorio.domain.acesso import TODOS_OS_MODULOS, Acesso
from relatorio.interfaces.web.dependencias import (
    ContextoWeb,
    Csrf,
    Ctx,
    redirecionar,
    situacao_coleta,
)
from relatorio.interfaces.web.seguranca import (
    CHAVE_MARCA,
    CHAVE_PERFIL,
    CHAVE_USUARIO,
    PERFIL_ADMIN,
    PERFIL_USUARIO,
    conferir_credenciais,
    conferir_senha,
    marca_da_senha,
)

log = structlog.get_logger(__name__)
router = APIRouter(include_in_schema=False)


@router.get("/health/live")
def viva() -> dict[str, str]:
    """Liveness para o deploy da Railway (não depende do worker)."""
    return {"status": "ok"}


@router.get("/health")
def saude(ctx: Ctx) -> JSONResponse:
    """200 com o horário do último ciclo; 503 se a coleta atrasar > 5 min no expediente."""
    try:
        coleta = situacao_coleta(ctx)
    except Exception as erro:  # banco inacessível
        log.error("health_banco_indisponivel", erro=type(erro).__name__)
        return JSONResponse({"status": "erro", "banco": "indisponível"}, status_code=503)
    corpo = {
        "status": "atrasado" if coleta.atrasada else "ok",
        "ultimo_ciclo_coleta": coleta.ultima.inicio.isoformat() if coleta.ultima else None,
        "em_horario_de_coleta": coleta.em_coleta,
        "coletor_habilitado": ctx.settings.coletor_habilitado,
        "agora": coleta.agora.isoformat(),
    }
    return JSONResponse(corpo, status_code=503 if coleta.atrasada else 200)


@router.get("/")
def raiz() -> RedirectResponse:
    return redirecionar("/admin")


@router.get("/login", response_class=HTMLResponse)
def pagina_login(request: Request, ctx: Ctx) -> HTMLResponse:
    return ctx.render(request, "admin/login.html", {"erro": None})


def autenticar(ctx: ContextoWeb, usuario: str, senha: str) -> tuple[Acesso, str, str] | None:
    """O administrador do deploy (variáveis de ambiente) ou um usuário cadastrado no painel."""
    admin = ctx.settings.admin_user
    if conferir_credenciais(
        usuario, senha, admin, ctx.settings.admin_password_hash.get_secret_value()
    ):
        return Acesso(admin, admin, TODOS_OS_MODULOS, administrador=True), PERFIL_ADMIN, ""
    # Sempre confere o bcrypt (com hash fictício se o usuário não existe): o tempo da
    # resposta não revela quais usuários existem.
    with ctx.container.uow() as uow:
        cadastrado = uow.usuarios.obter_por_usuario(usuario)
    if conferir_senha(senha, cadastrado.senha_hash if cadastrado else None) and cadastrado:
        acesso = Acesso(cadastrado.usuario, cadastrado.nome, frozenset(cadastrado.permissoes))
        return acesso, PERFIL_USUARIO, marca_da_senha(cadastrado.senha_hash)
    return None


@router.post("/login", response_model=None)
def entrar(
    request: Request,
    ctx: Ctx,
    _csrf: Csrf,
    usuario: Annotated[str, Form()] = "",
    senha: Annotated[str, Form()] = "",
) -> Response:
    ip = request.client.host if request.client else "?"
    chave = f"{ip}|{usuario.strip().lower()}"
    if ctx.tentativas.bloqueado(chave):
        log.warning("login_bloqueado", ip=ip)
        return ctx.render(
            request,
            "admin/login.html",
            {"erro": "Muitas tentativas. Aguarde 15 minutos e tente de novo."},
            status_code=429,
        )
    entrada = autenticar(ctx, usuario.strip(), senha)
    if entrada is None:
        restantes = ctx.tentativas.registrar_falha(chave)
        log.warning("login_falhou", ip=ip, restantes=restantes)
        erro = "Usuário ou senha inválidos."
        if restantes <= 2:
            erro += f" Restam {max(restantes, 0)} tentativa(s) antes do bloqueio."
        return ctx.render(request, "admin/login.html", {"erro": erro}, status_code=401)
    ctx.tentativas.limpar(chave)
    request.session.clear()  # nova sessão (evita fixação de sessão)
    acesso, perfil, marca = entrada
    request.session[CHAVE_USUARIO] = acesso.login
    request.session[CHAVE_PERFIL] = perfil
    request.session[CHAVE_MARCA] = marca
    log.info("login_ok", ip=ip, usuario=acesso.login, perfil=perfil)
    return redirecionar("/admin")


@router.post("/logout")
def sair(request: Request, _csrf: Csrf) -> RedirectResponse:
    request.session.clear()
    return redirecionar("/login")
