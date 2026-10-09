"""Exportação de relatórios por período: pedido, andamento (barra) e download.

O pedido responde na hora com o fragmento da barra; a planilha é gerada em segundo plano e
a barra consulta o andamento a cada segundo (HTMX). Cada tipo exige o módulo do relatório;
os atendimentos por médico ficam em Configurações (a planilha tem nomes de trabalhadores).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from relatorio.application.exportacao import ExportarRelatorio
from relatorio.application.modelos import Exportacao, StatusJob
from relatorio.domain import acesso as modulos
from relatorio.domain.acesso import Acesso
from relatorio.domain.exportacao import ATENDIMENTO, FINANCEIRO, MEDICOS, SESMT
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.dependencias import Csrf, Ctx, Usuario

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/exportacoes")

MODULO_DO_TIPO = {
    ATENDIMENTO: modulos.ATENDIMENTO,
    FINANCEIRO: modulos.FINANCEIRO,
    SESMT: modulos.SESMT,
    MEDICOS: modulos.CONFIGURACOES,
}
MAX_OPCOES = 2000  # tamanho da coluna exportacao.opcoes
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _exportar(ctx: Ctx) -> ExportarRelatorio:
    exportar = ctx.container.casos.exportar
    if exportar is None:  # pragma: no cover - o container real sempre monta
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)
    return exportar


def _exigir(acesso: Acesso, tipo: str) -> None:
    modulo = MODULO_DO_TIPO.get(tipo)
    if modulo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not acesso.pode(modulo):
        raise HTTPException(status.HTTP_403_FORBIDDEN)


def _fragmento(
    request: Request,
    ctx: Ctx,
    tipo: str,
    exportacao: Exportacao | None = None,
    erro: str | None = None,
    acao: str = "exportar",
) -> HTMLResponse:
    # Fragmento sem as mensagens da sessão (não consome os avisos da página).
    contexto = {
        "tipo": tipo,
        "e": exportacao,
        "erro": erro,
        "acao": exportacao.acao if exportacao else acao,
    }
    resposta = ctx.templates.TemplateResponse(request, "admin/_exportacao.html", contexto)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@router.post("", response_class=HTMLResponse)
def solicitar(
    request: Request,
    ctx: Ctx,
    acesso: Usuario,
    _csrf: Csrf,
    tarefas: BackgroundTasks,
    tipo: Annotated[str, Form()],
    inicio: Annotated[date, Form()],
    fim: Annotated[date, Form()],
    acao: Annotated[str, Form()] = "exportar",
    reconciliar: Annotated[bool, Form()] = False,
    filtrar_agendas: Annotated[bool, Form()] = False,
    agenda: Annotated[list[int] | None, Form()] = None,
) -> HTMLResponse:
    _exigir(acesso, tipo)
    # Só o atendimento tem opção (reler o SGG antes de recalcular); nos outros é ignorada.
    opcoes = "reconciliar" if reconciliar and tipo == ATENDIMENTO and acao == "processar" else ""
    if filtrar_agendas and tipo == ATENDIMENTO and acao == "exportar":
        if not agenda:
            return _fragmento(request, ctx, tipo, erro="Marque ao menos uma agenda.", acao=acao)
        with ctx.container.uow() as uow:
            conhecidas = {a.id_agenda for a in uow.agendas.listar()}
        if not set(agenda) <= conhecidas:
            return _fragmento(request, ctx, tipo, erro="Agenda desconhecida.", acao=acao)
        opcoes = ",".join(f"a{i}" for i in sorted(set(agenda)))
        if len(opcoes) > MAX_OPCOES:
            return _fragmento(
                request, ctx, tipo, erro="Muitas agendas marcadas para uma planilha.", acao=acao
            )
    try:
        exportacao = _exportar(ctx).solicitar(tipo, inicio, fim, acesso.login, acao, opcoes)
    except ValueError as erro:
        return _fragmento(request, ctx, tipo, erro=str(erro), acao=acao)
    tarefas.add_task(gerar_em_segundo_plano, ctx.container, exportacao.id, tipo)
    return _fragmento(request, ctx, tipo, exportacao)


def gerar_em_segundo_plano(container: Container, id_exportacao: str, tipo: str) -> None:
    exportar = container.casos.exportar
    if exportar is None:  # pragma: no cover
        return
    # O SESMT disputa a cota da API com a coleta noturna: usa a mesma trava do job.
    exportacao = exportar.obter(id_exportacao)
    processando = exportacao is not None and exportacao.acao == "processar"
    trava = f"exportar_{tipo}"
    if tipo == SESMT:
        trava = "consolidar_sesmt"
    elif processando:  # a mesma trava dos jobs que gravam esses resumos
        trava = {
            ATENDIMENTO: "consolidar_dia",
            FINANCEIRO: "consolidar_financeiro",
            MEDICOS: "processar_exames",
        }[tipo]
    job = f"{'processar_periodo' if processando else 'exportar'}_{tipo}"
    resultado = container.executor.executar(
        job, lambda: exportar.executar(id_exportacao), trava=trava
    )
    if resultado is StatusJob.IGNORADO:
        exportar.marcar_ocupado(id_exportacao)


@router.get("/{id_exportacao}", response_class=HTMLResponse)
def andamento(id_exportacao: str, request: Request, ctx: Ctx, acesso: Usuario) -> HTMLResponse:
    exportacao = _exportar(ctx).obter(id_exportacao)
    if exportacao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _exigir(acesso, exportacao.tipo)
    return _fragmento(request, ctx, exportacao.tipo, exportacao)


@router.get("/{id_exportacao}/arquivo")
def baixar(id_exportacao: str, ctx: Ctx, acesso: Usuario) -> Response:
    exportar = _exportar(ctx)
    exportacao = exportar.obter(id_exportacao)
    if exportacao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _exigir(acesso, exportacao.tipo)
    with ctx.container.uow() as uow:
        arquivo = uow.exportacoes.arquivo(id_exportacao)
    if arquivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Arquivo expirado ou não gerado")
    nome, conteudo = arquivo
    log.info("exportacao_baixada", id=id_exportacao, tipo=exportacao.tipo, por=acesso.login)
    return Response(
        conteudo,
        media_type=TIPO_XLSX,
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "Cache-Control": "no-store",
        },
    )
