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
) -> HTMLResponse:
    # Fragmento sem as mensagens da sessão (não consome os avisos da página).
    resposta = ctx.templates.TemplateResponse(
        request, "admin/_exportacao.html", {"tipo": tipo, "e": exportacao, "erro": erro}
    )
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
) -> HTMLResponse:
    _exigir(acesso, tipo)
    try:
        exportacao = _exportar(ctx).solicitar(tipo, inicio, fim, acesso.login)
    except ValueError as erro:
        return _fragmento(request, ctx, tipo, erro=str(erro))
    tarefas.add_task(gerar_em_segundo_plano, ctx.container, exportacao.id, tipo)
    return _fragmento(request, ctx, tipo, exportacao)


def gerar_em_segundo_plano(container: Container, id_exportacao: str, tipo: str) -> None:
    exportar = container.casos.exportar
    if exportar is None:  # pragma: no cover
        return
    # O SESMT disputa a cota da API com a coleta noturna: usa a mesma trava do job.
    trava = "consolidar_sesmt" if tipo == SESMT else f"exportar_{tipo}"
    resultado = container.executor.executar(
        f"exportar_{tipo}", lambda: exportar.executar(id_exportacao), trava=trava
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
