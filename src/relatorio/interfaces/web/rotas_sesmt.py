"""Admin do relatório do SESMT: histórico, prévia, reprocessamento, destinatários e nomes
dos grupos de clientes. Todas as rotas exigem login."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from relatorio.application.sesmt import (
    CHAVE_NOMES_GRUPOS,
    formatar_nomes_grupos,
    ler_nomes_grupos,
)
from relatorio.domain.acesso import email_valido
from relatorio.domain.sesmt import da_versao_atual
from relatorio.infrastructure.container import Container
from relatorio.interfaces.demo import html_para_navegador
from relatorio.interfaces.web.dashboard import montar_sesmt
from relatorio.interfaces.web.dependencias import Csrf, Ctx, UsuarioSesmt, mensagem, redirecionar
from relatorio.interfaces.web.paginacao import paginar, voltar_para

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/sesmt")

JOB_REPROCESSAR_SESMT = "reprocessar_sesmt"
URL = "/admin/sesmt"
URL_GESTAO = f"{URL}/gestao"
DIAS_HISTORICO = 90
CSP_PREVIA = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, ctx: Ctx, _usuario: UsuarioSesmt) -> HTMLResponse:
    """A última coleta (o mesmo do e-mail), com as listas paginadas, e o histórico."""
    hoje = ctx.hoje()
    with ctx.container.uow() as uow:
        repositorio = uow.resumos_sesmt
        recentes = [r for r in repositorio.listar_recentes(2) if da_versao_atual(r.metricas)]
        historico = repositorio.listar_indicadores(
            hoje - timedelta(days=DIAS_HISTORICO), hoje, (("versao_regra",), ("vencidos", "total"))
        )
    painel = None
    if recentes:
        painel = montar_sesmt(
            recentes[0].metricas,
            recentes[1].metricas if len(recentes) > 1 else None,
            [(dia, m["vencidos"]["total"]) for dia, m in historico if da_versao_atual(m)],
        )
    return ctx.render(
        request,
        "admin/dashboard_sesmt.html",
        {
            "pagina": "dashboard_sesmt",
            "p": painel,
            "pg_vencem": paginar(request, painel.vencem if painel else [], "vencem"),
            "pg_rejeitados": paginar(request, painel.rejeitados if painel else [], "rejeitados"),
            "url_gestao": URL_GESTAO,
            "habilitado": ctx.settings.sesmt_habilitado,
        },
    )


@router.get("/gestao", response_class=HTMLResponse)
def sesmt(request: Request, ctx: Ctx, _usuario: UsuarioSesmt) -> HTMLResponse:
    with ctx.container.uow() as uow:
        resumos = uow.resumos_sesmt.listar_recentes(14)
        destinatarios = uow.destinatarios_sesmt.listar()
        texto = uow.configuracoes.obter_todas().get(CHAVE_NOMES_GRUPOS)
    return ctx.render(
        request,
        "admin/sesmt.html",
        {
            "pagina": "sesmt",
            "resumos": resumos,
            "destinatarios": destinatarios,
            "pg_destinatarios": paginar(request, destinatarios, "destinatarios"),
            "ativos": sum(1 for d in destinatarios if d.ativo),
            "habilitado": ctx.settings.sesmt_habilitado,
            "ontem": ctx.hoje() - timedelta(days=1),
            "hoje": ctx.hoje(),
            "override": ctx.settings.destinatarios_override,
            "nomes_grupos": formatar_nomes_grupos(ler_nomes_grupos(texto)),
        },
    )


@router.get("/relatorios/{dia}", response_class=HTMLResponse)
def ver_relatorio_sesmt(dia: date, ctx: Ctx, _usuario: UsuarioSesmt) -> HTMLResponse:
    with ctx.container.uow() as uow:
        registro = uow.resumos_sesmt.obter(dia)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resumo do SESMT não encontrado")
    if not da_versao_atual(registro.metricas):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Resumo no formato antigo do relatório: gere a data de novo"
        )
    conteudo = ctx.container.renderizador.relatorio_sesmt(registro.metricas)
    return HTMLResponse(
        html_para_navegador(conteudo), headers={"Content-Security-Policy": CSP_PREVIA}
    )


@router.post("/reprocessar")
def reprocessar_sesmt(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioSesmt,
    _csrf: Csrf,
    tarefas: BackgroundTasks,
    dia: Annotated[date, Form(alias="data")],
    enviar: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    if dia > ctx.hoje():
        mensagem(request, "Escolha uma data até hoje.", "erro")
        return redirecionar(URL_GESTAO)
    tarefas.add_task(reprocessar_em_segundo_plano, ctx.container, dia, enviar)
    log.info("sesmt_reprocessamento_agendado", referencia=dia.isoformat(), enviar=enviar)
    mensagem(
        request,
        f"Coleta do SESMT de {dia:%d/%m/%Y} iniciada. São cerca de 4.300 consultas ao SGG e leva"
        " cerca de 3h30. O resultado aparece nesta página e em Execuções.",
    )
    return redirecionar(URL_GESTAO)


def reprocessar_em_segundo_plano(container: Container, dia: date, enviar: bool) -> None:
    casos = container.casos
    consolidar, envio = casos.consolidar_sesmt, casos.enviar_sesmt
    if consolidar is None or envio is None:
        return

    def tarefa() -> dict[str, object]:
        detalhe = dict(consolidar.executar(dia))
        if enviar:
            resultado = envio.executar(dia, forcar=True)
            detalhe["envio"] = resultado.get("status")
            if resultado.get("status") == "enviado":
                detalhe["destinatarios"] = resultado.get("destinatarios", 0)
        return detalhe

    container.executor.executar(JOB_REPROCESSAR_SESMT, tarefa)


@router.post("/destinatarios")
def adicionar_destinatario_sesmt(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioSesmt,
    _csrf: Csrf,
    email: Annotated[str, Form()] = "",
    nome: Annotated[str, Form()] = "",
) -> RedirectResponse:
    email = email.strip().lower()
    if not email_valido(email):
        mensagem(request, "Informe um e-mail válido.", "erro")
    else:
        with ctx.container.uow() as uow:
            uow.destinatarios_sesmt.adicionar(email, nome.strip()[:120] or None)
            uow.commit()
        mensagem(request, f"{email} cadastrado na lista do relatório do SESMT.")
    return redirecionar(voltar_para(request, f"{URL_GESTAO}#destinatarios"))


@router.post("/destinatarios/{id_destinatario}/ativo", response_class=HTMLResponse)
def alternar_destinatario_sesmt(
    request: Request,
    id_destinatario: int,
    ctx: Ctx,
    _usuario: UsuarioSesmt,
    _csrf: Csrf,
    ativo: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    with ctx.container.uow() as uow:
        uow.destinatarios_sesmt.definir_ativo(id_destinatario, ativo)
        uow.commit()
        destinatario = next(
            (d for d in uow.destinatarios_sesmt.listar() if d.id == id_destinatario), None
        )
    if destinatario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    log.info("destinatario_sesmt_alterado", id=id_destinatario, ativo=ativo)
    return ctx.render(
        request, "admin/_linha_destinatario.html", {"d": destinatario, "url_base": URL}
    )


@router.post("/grupos")
def salvar_nomes_grupos(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioSesmt,
    _csrf: Csrf,
    nomes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    lidos = ler_nomes_grupos(nomes[:2000])
    with ctx.container.uow() as uow:
        uow.configuracoes.definir(CHAVE_NOMES_GRUPOS, formatar_nomes_grupos(lidos))
        uow.commit()
    mensagem(
        request,
        "Nomes dos grupos salvos. Valem a partir da próxima coleta"
        " (reprocesse uma data para ver agora).",
    )
    return redirecionar(URL_GESTAO)
