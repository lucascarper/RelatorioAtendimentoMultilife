"""Admin do relatório financeiro: histórico, prévia, reprocessamento, destinatários e
nomes dos centros de custo. Todas as rotas exigem login."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from relatorio.application.financeiro import (
    CHAVE_NOMES_CENTROS,
    formatar_nomes_centros,
    ler_nomes_centros,
)
from relatorio.infrastructure.container import Container
from relatorio.interfaces.demo import html_para_navegador
from relatorio.interfaces.web.dependencias import (
    Csrf,
    Ctx,
    UsuarioFinanceiro,
    mensagem,
    redirecionar,
)
from relatorio.interfaces.web.rotas_admin import EMAIL_VALIDO

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/financeiro")

JOB_REPROCESSAR_FINANCEIRO = "reprocessar_financeiro"
URL = "/admin/financeiro"
CSP_PREVIA = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"


def _email_valido(email: str) -> bool:
    # Mesma regra do cadastro de destinatários do relatório de atendimentos.
    return bool(EMAIL_VALIDO.match(email)) and len(email) <= 254


@router.get("", response_class=HTMLResponse)
def financeiro(request: Request, ctx: Ctx, _usuario: UsuarioFinanceiro) -> HTMLResponse:
    with ctx.container.uow() as uow:
        resumos = uow.resumos_financeiros.listar_recentes(14)
        destinatarios = uow.destinatarios_financeiro.listar()
        nomes = ler_nomes_centros(uow.configuracoes.obter_todas().get(CHAVE_NOMES_CENTROS, ""))
    centros = sorted(
        {c["centro"] for r in resumos[:1] for c in r.metricas.get("rateio", {}).get("centros", [])}
    )
    return ctx.render(
        request,
        "admin/financeiro.html",
        {
            "pagina": "financeiro",
            "resumos": resumos,
            "destinatarios": destinatarios,
            "ativos": sum(1 for d in destinatarios if d.ativo),
            "habilitado": ctx.settings.financeiro_habilitado,
            "ontem": ctx.hoje() - timedelta(days=1),
            "override": ctx.settings.destinatarios_override,
            "nomes_centros": formatar_nomes_centros(nomes),
            "centros_no_ultimo": centros,
        },
    )


@router.get("/relatorios/{dia}", response_class=HTMLResponse)
def ver_relatorio_financeiro(dia: date, ctx: Ctx, _usuario: UsuarioFinanceiro) -> HTMLResponse:
    with ctx.container.uow() as uow:
        registro = uow.resumos_financeiros.obter(dia)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resumo financeiro não encontrado")
    conteudo = ctx.container.renderizador.relatorio_financeiro(registro.metricas)
    return HTMLResponse(
        html_para_navegador(conteudo), headers={"Content-Security-Policy": CSP_PREVIA}
    )


@router.post("/reprocessar")
def reprocessar_financeiro(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioFinanceiro,
    _csrf: Csrf,
    tarefas: BackgroundTasks,
    dia: Annotated[date, Form(alias="data")],
    enviar: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    if dia >= ctx.hoje():
        mensagem(request, "O relatório financeiro é do dia anterior: escolha até ontem.", "erro")
        return redirecionar(URL)
    tarefas.add_task(reprocessar_em_segundo_plano, ctx.container, dia, enviar)
    log.info("financeiro_reprocessamento_agendado", referencia=dia.isoformat(), enviar=enviar)
    mensagem(
        request,
        f"Coleta financeira de {dia:%d/%m/%Y} iniciada (cerca de 1 minuto). O resultado aparece"
        " nesta página e em Execuções.",
    )
    return redirecionar(URL)


def reprocessar_em_segundo_plano(container: Container, dia: date, enviar: bool) -> None:
    casos = container.casos
    consolidar, envio = casos.consolidar_financeiro, casos.enviar_financeiro
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

    container.executor.executar(JOB_REPROCESSAR_FINANCEIRO, tarefa)


@router.post("/destinatarios")
def adicionar_destinatario_financeiro(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioFinanceiro,
    _csrf: Csrf,
    email: Annotated[str, Form()] = "",
    nome: Annotated[str, Form()] = "",
) -> RedirectResponse:
    email = email.strip().lower()
    if not _email_valido(email):
        mensagem(request, "Informe um e-mail válido.", "erro")
    else:
        with ctx.container.uow() as uow:
            uow.destinatarios_financeiro.adicionar(email, nome.strip()[:120] or None)
            uow.commit()
        mensagem(request, f"{email} cadastrado na lista do relatório financeiro.")
    return redirecionar(URL)


@router.post("/destinatarios/{id_destinatario}/ativo", response_class=HTMLResponse)
def alternar_destinatario_financeiro(
    request: Request,
    id_destinatario: int,
    ctx: Ctx,
    _usuario: UsuarioFinanceiro,
    _csrf: Csrf,
    ativo: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    with ctx.container.uow() as uow:
        uow.destinatarios_financeiro.definir_ativo(id_destinatario, ativo)
        uow.commit()
        destinatario = next(
            (d for d in uow.destinatarios_financeiro.listar() if d.id == id_destinatario), None
        )
    if destinatario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    log.info("destinatario_financeiro_alterado", id=id_destinatario, ativo=ativo)
    return ctx.render(
        request, "admin/_linha_destinatario.html", {"d": destinatario, "url_base": URL}
    )


@router.post("/centros")
def salvar_nomes_centros(
    request: Request,
    ctx: Ctx,
    _usuario: UsuarioFinanceiro,
    _csrf: Csrf,
    nomes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    lidos = ler_nomes_centros(nomes[:2000])
    with ctx.container.uow() as uow:
        uow.configuracoes.definir(CHAVE_NOMES_CENTROS, formatar_nomes_centros(lidos))
        uow.commit()
    mensagem(
        request,
        "Nomes dos centros de custo salvos. Valem a partir da próxima coleta"
        " (reprocesse uma data para ver agora).",
    )
    return redirecionar(URL)
