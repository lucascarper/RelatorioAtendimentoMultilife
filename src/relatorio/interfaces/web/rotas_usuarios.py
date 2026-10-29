"""Gerenciamento de usuários e permissões por módulo (dentro de Configurações).

Cadastro simples (nome, usuário e senha) e, abaixo, o acesso a cada módulo do sistema. O
administrador do deploy (``ADMIN_USER``) não é editado aqui: tem sempre todos os módulos.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from relatorio.application.ports import EmailDuplicado, ErroIntegracao, UsuarioDuplicado
from relatorio.application.recuperacao_senha import VALIDADE_CONVITE
from relatorio.domain.acesso import (
    CONFIGURACOES,
    MODULOS,
    ROTULO_MODULO,
    SENHA_MIN,
    email_valido,
    modulos_validos,
    validar_nome,
    validar_senha,
    validar_usuario,
)
from relatorio.interfaces.web.dependencias import (
    Csrf,
    Ctx,
    UsuarioConfiguracoes,
    mensagem,
    redirecionar,
)
from relatorio.interfaces.web.paginacao import paginar, voltar_para
from relatorio.interfaces.web.rotas_senha import enviar_link
from relatorio.interfaces.web.seguranca import (
    CHAVE_MARCA,
    CHAVE_USUARIO,
    gerar_hash_senha,
    marca_da_senha,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/configuracoes/usuarios")

URL = "/admin/configuracoes/usuarios"
DUPLICADO = {
    UsuarioDuplicado: "Já existe um usuário com esse login.",
    EmailDuplicado: "Já existe um usuário com esse e-mail.",
}


@router.get("", response_class=HTMLResponse)
def lista(request: Request, ctx: Ctx, acesso: UsuarioConfiguracoes) -> HTMLResponse:
    with ctx.container.uow() as uow:
        usuarios = uow.usuarios.listar()
    return ctx.render(
        request,
        "admin/usuarios.html",
        {
            "pagina": "usuarios",
            "usuarios": usuarios,
            "pg_usuarios": paginar(request, usuarios, "usuarios"),
            "rotulos": ROTULO_MODULO,
            "administrador": ctx.settings.admin_user,
            "eu": None if acesso.administrador else acesso.login,
        },
    )


def _formulario(
    request: Request,
    ctx: Ctx,
    *,
    id_usuario: int | None,
    valores: dict[str, Any],
    erro: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return ctx.render(
        request,
        "admin/usuario_form.html",
        {
            "pagina": "usuarios",
            "id_usuario": id_usuario,
            "valores": valores,
            "erro": erro,
            "modulos": MODULOS,
            "senha_minima": SENHA_MIN,
        },
        status_code=status_code,
    )


@router.get("/novo", response_class=HTMLResponse)
def novo(request: Request, ctx: Ctx, _acesso: UsuarioConfiguracoes) -> HTMLResponse:
    return _formulario(
        request,
        ctx,
        id_usuario=None,
        valores={"nome": "", "usuario": "", "email": "", "modulos": set()},
    )


def _validar_email(email: str) -> str | None:
    """E-mail é opcional; sem ele não dá para enviar o acesso nem recuperar a senha."""
    email = email.strip().lower()
    if email and not email_valido(email):
        raise ValueError("Informe um e-mail válido (ou deixe o campo em branco).")
    return email or None


def _validar(
    ctx: Ctx, nome: str, usuario: str, senha: str, modulos: list[str] | None, *, nova: bool
) -> tuple[str, str, str | None, tuple[str, ...]]:
    """Devolve (nome, usuário, senha ou None se não mudou, módulos) ou lança ValueError."""
    nome_ok = validar_nome(nome)
    usuario_ok = validar_usuario(usuario)
    if usuario_ok == ctx.settings.admin_user.strip().lower():
        raise ValueError("Esse usuário é reservado ao administrador do sistema. Escolha outro.")
    senha_ok = validar_senha(senha) if (nova or senha) else None
    return nome_ok, usuario_ok, senha_ok, modulos_validos(modulos or ())


@router.post("", response_class=HTMLResponse, response_model=None)
def criar(
    request: Request,
    ctx: Ctx,
    acesso: UsuarioConfiguracoes,
    _csrf: Csrf,
    nome: Annotated[str, Form()] = "",
    usuario: Annotated[str, Form()] = "",
    senha: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    modulos: Annotated[list[str] | None, Form()] = None,
) -> Response:
    valores = {"nome": nome, "usuario": usuario, "email": email, "modulos": set(modulos or ())}
    try:
        nome_ok, usuario_ok, senha_ok, permissoes = _validar(
            ctx, nome, usuario, senha, modulos, nova=True
        )
        email_ok = _validar_email(email)
        assert senha_ok is not None  # noqa: S101 — nova=True sempre valida a senha
        with ctx.container.uow() as uow:
            uow.usuarios.criar(
                nome_ok, usuario_ok, gerar_hash_senha(senha_ok), permissoes, email_ok
            )
            uow.commit()
    except ValueError as erro:
        return _formulario(
            request, ctx, id_usuario=None, valores=valores, erro=str(erro), status_code=422
        )
    except (UsuarioDuplicado, EmailDuplicado) as erro:
        return _formulario(
            request,
            ctx,
            id_usuario=None,
            valores=valores,
            erro=DUPLICADO[type(erro)],
            status_code=422,
        )
    log.info("usuario_criado", usuario=usuario_ok, modulos=permissoes, por=acesso.login)
    mensagem(request, f"Usuário {usuario_ok} criado.")
    return redirecionar(URL)


@router.get("/{id_usuario}/editar", response_class=HTMLResponse)
def editar(
    id_usuario: int, request: Request, ctx: Ctx, _acesso: UsuarioConfiguracoes
) -> HTMLResponse:
    with ctx.container.uow() as uow:
        atual = uow.usuarios.obter(id_usuario)
    if atual is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    valores = {
        "nome": atual.nome,
        "usuario": atual.usuario,
        "email": atual.email or "",
        "modulos": set(atual.permissoes),
    }
    return _formulario(request, ctx, id_usuario=id_usuario, valores=valores)


@router.post("/{id_usuario}", response_class=HTMLResponse, response_model=None)
def atualizar(
    id_usuario: int,
    request: Request,
    ctx: Ctx,
    acesso: UsuarioConfiguracoes,
    _csrf: Csrf,
    nome: Annotated[str, Form()] = "",
    usuario: Annotated[str, Form()] = "",
    senha: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    modulos: Annotated[list[str] | None, Form()] = None,
) -> Response:
    with ctx.container.uow() as uow:
        atual = uow.usuarios.obter(id_usuario)
    if atual is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    valores = {"nome": nome, "usuario": usuario, "email": email, "modulos": set(modulos or ())}
    eu = not acesso.administrador and acesso.login == atual.usuario
    try:
        nome_ok, usuario_ok, senha_ok, permissoes = _validar(
            ctx, nome, usuario, senha, modulos, nova=False
        )
        email_ok = _validar_email(email)
        if eu and CONFIGURACOES not in permissoes:
            raise ValueError("Você não pode tirar o seu próprio acesso a Configurações.")
        novo_hash = gerar_hash_senha(senha_ok) if senha_ok else None
        with ctx.container.uow() as uow:
            uow.usuarios.atualizar(
                id_usuario, nome_ok, usuario_ok, permissoes, novo_hash, email=email_ok
            )
            uow.commit()
    except ValueError as erro:
        return _formulario(
            request, ctx, id_usuario=id_usuario, valores=valores, erro=str(erro), status_code=422
        )
    except (UsuarioDuplicado, EmailDuplicado) as erro:
        return _formulario(
            request,
            ctx,
            id_usuario=id_usuario,
            valores=valores,
            erro=DUPLICADO[type(erro)],
            status_code=422,
        )
    if eu:  # mudou o próprio login ou a própria senha: a sessão acompanha
        request.session[CHAVE_USUARIO] = usuario_ok
        if novo_hash:
            request.session[CHAVE_MARCA] = marca_da_senha(novo_hash)
    log.info(
        "usuario_alterado",
        usuario=usuario_ok,
        modulos=permissoes,
        senha_trocada=bool(senha_ok),
        por=acesso.login,
    )
    mensagem(request, f"Usuário {usuario_ok} atualizado.")
    return redirecionar(URL)


@router.post("/{id_usuario}/enviar-acesso", response_model=None)
def enviar_acesso(
    id_usuario: int, request: Request, ctx: Ctx, acesso: UsuarioConfiguracoes, _csrf: Csrf
) -> Response:
    """Manda ao e-mail do usuário o login e um link para ele definir a senha."""
    with ctx.container.uow() as uow:
        atual = uow.usuarios.obter(id_usuario)
    if atual is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    if not atual.email:
        mensagem(request, f"Cadastre o e-mail de {atual.usuario} antes de enviar o acesso.", "erro")
    else:
        try:
            enviar_link(ctx, request, atual, VALIDADE_CONVITE, convite=True)
        except (ErroIntegracao, OSError) as erro:
            log.error("acesso_falha_email", usuario=atual.usuario, erro=str(erro))
            mensagem(request, "Não foi possível enviar o e-mail agora. Tente de novo.", "erro")
        else:
            log.info("acesso_enviado", usuario=atual.usuario, por=acesso.login)
            mensagem(request, f"Acesso de {atual.usuario} enviado para {atual.email}.")
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": voltar_para(request, URL)})
    return redirecionar(voltar_para(request, URL))


@router.post("/{id_usuario}/excluir", response_model=None)
def excluir(
    id_usuario: int, request: Request, ctx: Ctx, acesso: UsuarioConfiguracoes, _csrf: Csrf
) -> Response:
    with ctx.container.uow() as uow:
        atual = uow.usuarios.obter(id_usuario)
        if atual is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
        if not acesso.administrador and acesso.login == atual.usuario:
            mensagem(request, "Você não pode excluir o seu próprio usuário.", "erro")
        else:
            uow.usuarios.excluir(id_usuario)
            uow.commit()
            log.info("usuario_excluido", usuario=atual.usuario, por=acesso.login)
            mensagem(request, f"Usuário {atual.usuario} excluído.")
    if request.headers.get("HX-Request"):  # a lista inteira recarrega, com a mensagem
        return Response(status_code=200, headers={"HX-Redirect": voltar_para(request, URL)})
    return redirecionar(voltar_para(request, URL))
