"""Médicos dos atendimentos por médico (dentro de Configurações).

Lista os médicos que apareceram nos exames clínicos do SGG; quem estiver marcado entra na
seção do e-mail de atendimentos e na planilha anexa. Também permite cadastrar um CRM que
ainda não apareceu e processar os exames de uma data na hora.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from relatorio.application.exames import JOB_PROCESSAR_EXAMES
from relatorio.application.ports import ErroIntegracao
from relatorio.domain.exames import normalizar_crm
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.dependencias import (
    Csrf,
    Ctx,
    UsuarioConfiguracoes,
    mensagem,
    redirecionar,
)
from relatorio.interfaces.web.paginacao import paginar

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/configuracoes/medicos")

URL = "/admin/configuracoes/medicos"


@router.get("", response_class=HTMLResponse)
def medicos(request: Request, ctx: Ctx, _usuario: UsuarioConfiguracoes) -> HTMLResponse:
    with ctx.container.uow() as uow:
        lista = uow.medicos.listar()
        coletas = uow.exames.coletas_recentes(14)
    return ctx.render(
        request,
        "admin/medicos.html",
        {
            "pagina": "medicos",
            "medicos": lista,
            "pg_medicos": paginar(request, lista, "medicos"),
            "pg_coletas": paginar(request, coletas, "coletas"),
            "escolhidos": sum(1 for m in lista if m.selecionado),
            "coletas": coletas,
            "hoje": ctx.hoje(),
        },
    )


@router.post("/{crm}/selecionado", response_class=HTMLResponse)
def alternar_medico(
    request: Request,
    crm: str,
    ctx: Ctx,
    _usuario: UsuarioConfiguracoes,
    _csrf: Csrf,
    selecionado: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    with ctx.container.uow() as uow:
        uow.medicos.definir_selecionado(crm, selecionado)
        uow.commit()
        medico = next((m for m in uow.medicos.listar() if m.crm == crm), None)
    if medico is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    log.info("medico_alterado", crm=crm, selecionado=selecionado)
    return ctx.render(request, "admin/_linha_medico.html", {"m": medico})


@router.post("")
def adicionar_medico(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioConfiguracoes,
    _csrf: Csrf,
    crm: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        crm_ok = normalizar_crm(crm)
    except ValueError as erro:
        mensagem(request, str(erro), "erro")
        return redirecionar(URL)
    try:
        medico = ctx.container.sgg.medico(crm_ok)
    except ErroIntegracao as erro:
        mensagem(request, f"SGG indisponível: {erro}", "erro")
        return redirecionar(URL)
    if medico is None:
        mensagem(request, f"O CRM {crm_ok} não está no cadastro de médicos do SGG.", "erro")
        return redirecionar(URL)
    with ctx.container.uow() as uow:
        uow.medicos.adicionar(medico)
        uow.commit()
    log.info("medico_adicionado", crm=crm_ok)
    mensagem(request, f"{medico.nome} (CRM {medico.crm}) entra nos atendimentos por médico.")
    return redirecionar(URL)


@router.post("/processar")
def processar(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioConfiguracoes,
    _csrf: Csrf,
    tarefas: BackgroundTasks,
    dia: Annotated[date, Form(alias="data")],
) -> RedirectResponse:
    if dia > ctx.hoje():
        mensagem(request, "Não é possível processar uma data futura.", "erro")
        return redirecionar(URL)
    tarefas.add_task(processar_em_segundo_plano, ctx.container, dia)
    log.info("exames_processamento_pelo_admin", referencia=dia.isoformat())
    mensagem(
        request,
        f"Processamento dos exames de {dia:%d/%m/%Y} iniciado. Atualize a página em instantes;"
        " o resultado também aparece em Execuções.",
    )
    return redirecionar(URL)


def processar_em_segundo_plano(container: Container, dia: date) -> None:
    processar = container.casos.processar_exames
    if processar is None:  # pragma: no cover - o container real sempre monta
        return
    container.executor.executar(JOB_PROCESSAR_EXAMES, lambda: processar.executar(dia))
