"""Rotas do admin (RF07/RF08): painel, reprocessamento, destinatários, unidades,
agendas, configurações e histórico de execuções. Todas exigem login."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, time, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from relatorio.application.configuracao import (
    CHAVE_ATIPICO_MAX,
    CHAVE_ATIPICO_MIN,
    CHAVE_EMAIL_TECNICO,
    CHAVE_MANHA_INICIO,
    CHAVE_TARDE_FIM,
    CHAVE_TARDE_INICIO,
    CHAVE_UNIDADES,
)
from relatorio.application.ports import ErroIntegracao
from relatorio.domain.metricas import RegrasMetricas
from relatorio.domain.turnos import ConfiguracaoTurnos
from relatorio.interfaces.demo import html_para_navegador
from relatorio.interfaces.web.dependencias import (
    Csrf,
    Ctx,
    Usuario,
    mensagem,
    redirecionar,
    situacao_coleta,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin")

EMAIL_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
JOBS = {
    "coletar_ciclo": "Coleta (a cada 60 s)",
    "reconciliar_dia": "Reconciliação do dia (18:30)",
    "sync_agendas": "Sincronização de agendas (05:30)",
    "consolidar_dia": "Consolidação (23:00)",
    "enviar_relatorio": "Envio do e-mail (07:59)",
    "verificar_envio": "Verificação do envio (08:10)",
    "limpar_retencao": "Retenção de 24 meses (dia 1)",
}
ROTULOS_DETALHE = {
    "requisicoes": "requisições",
    "eventos_novos": "eventos novos",
    "saltos": "saltos de status",
    "referencia": "referência",
    "sem_movimento": "sem movimento",
    "faltas_confirmadas_na_agenda": "faltas confirmadas na agenda",
    "verificacao_faltas_indisponivel": "conferência de faltas indisponível",
    "status": "situação",
    "destinatarios": "destinatários",
    "eventos": "eventos apagados",
    "snapshots": "snapshots apagados",
    "resumos": "resumos apagados",
    "execucoes": "execuções apagadas",
}
CSP_PREVIA = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"


# ------------------------------------------------------------------ painel e relatórios


@router.get("", response_class=HTMLResponse)
def painel(request: Request, ctx: Ctx, _usuario: Usuario) -> HTMLResponse:
    coleta = situacao_coleta(ctx)
    with ctx.container.uow() as uow:
        resumos = uow.resumos.listar_recentes(14)
        falhas_seguidas = uow.execucoes.falhas_consecutivas("coletar_ciclo")
        ativos = uow.destinatarios.listar(apenas_ativos=True)
    return ctx.render(
        request,
        "admin/painel.html",
        {
            "pagina": "painel",
            "resumos": resumos,
            "ultimo_sucesso": coleta.ultima,
            "coleta_atrasada": coleta.atrasada,
            "em_coleta": coleta.em_coleta,
            "falhas_seguidas": falhas_seguidas,
            "destinatarios_ativos": len(ativos),
            "coletor_habilitado": ctx.settings.coletor_habilitado,
            "ontem": ctx.hoje() - timedelta(days=1),
            "override": ctx.settings.destinatarios_override,
        },
    )


@router.get("/relatorios/{dia}", response_class=HTMLResponse)
def ver_relatorio(dia: date, ctx: Ctx, _usuario: Usuario) -> HTMLResponse:
    with ctx.container.uow() as uow:
        registro = uow.resumos.obter(dia)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resumo não encontrado")
    conteudo = ctx.container.renderizador.relatorio(registro.metricas)
    # A prévia é o próprio e-mail (CSS inline): CSP própria, sem scripts.
    return HTMLResponse(
        html_para_navegador(conteudo), headers={"Content-Security-Policy": CSP_PREVIA}
    )


@router.post("/relatorios/reprocessar")
def reprocessar(
    request: Request,
    ctx: Ctx,
    _usuario: Usuario,
    _csrf: Csrf,
    dia: Annotated[date, Form(alias="data")],
    reconciliar: Annotated[bool, Form()] = False,
    enviar: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    if dia > ctx.hoje():
        mensagem(request, "Não é possível reprocessar uma data futura.", "erro")
        return redirecionar("/admin")
    try:
        detalhe = ctx.container.casos.reprocessar.executar(
            dia, reconciliar=reconciliar, enviar=enviar
        )
    except Exception as erro:
        log.exception("reprocessar_falhou", referencia=dia.isoformat())
        mensagem(request, f"Falha ao reprocessar {dia:%d/%m/%Y}: {erro}", "erro")
        return redirecionar("/admin")
    texto = f"{dia:%d/%m/%Y} reprocessado."
    envio = detalhe.get("envio")
    if isinstance(envio, dict):
        texto += (
            f" E-mail enviado para {envio.get('destinatarios', 0)} destinatário(s)."
            if envio.get("status") == "enviado"
            else f" Envio: {envio.get('status')}."
        )
    if isinstance(detalhe.get("reconciliacao"), str):
        texto += " SGG indisponível: recalculado com os eventos já coletados."
    log.info("reprocessado_pelo_admin", referencia=dia.isoformat(), enviar=enviar)
    mensagem(request, texto)
    return redirecionar("/admin")


# ------------------------------------------------------------------ destinatários


@router.get("/destinatarios", response_class=HTMLResponse)
def destinatarios(request: Request, ctx: Ctx, _usuario: Usuario) -> HTMLResponse:
    with ctx.container.uow() as uow:
        lista = uow.destinatarios.listar()
    return ctx.render(
        request,
        "admin/destinatarios.html",
        {
            "pagina": "destinatarios",
            "destinatarios": lista,
            "override": ctx.settings.destinatarios_override,
        },
    )


@router.post("/destinatarios")
def adicionar_destinatario(
    request: Request,
    ctx: Ctx,
    _usuario: Usuario,
    _csrf: Csrf,
    email: Annotated[str, Form()] = "",
    nome: Annotated[str, Form()] = "",
) -> RedirectResponse:
    email = email.strip().lower()
    if not EMAIL_VALIDO.match(email) or len(email) > 254:
        mensagem(request, "Informe um e-mail válido.", "erro")
    else:
        with ctx.container.uow() as uow:
            uow.destinatarios.adicionar(email, nome.strip()[:120] or None)
            uow.commit()
        mensagem(request, f"{email} cadastrado e ativo.")
    return redirecionar("/admin/destinatarios")


@router.post("/destinatarios/{id_destinatario}/ativo", response_class=HTMLResponse)
def alternar_destinatario(
    request: Request,
    id_destinatario: int,
    ctx: Ctx,
    _usuario: Usuario,
    _csrf: Csrf,
    ativo: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    with ctx.container.uow() as uow:
        uow.destinatarios.definir_ativo(id_destinatario, ativo)
        uow.commit()
        destinatario = next(
            (d for d in uow.destinatarios.listar() if d.id == id_destinatario), None
        )
    if destinatario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    log.info("destinatario_alterado", id=id_destinatario, ativo=ativo)
    return ctx.render(request, "admin/_linha_destinatario.html", {"d": destinatario})


# ------------------------------------------------------------------ unidades e agendas


@router.get("/agendas", response_class=HTMLResponse)
def agendas(request: Request, ctx: Ctx, _usuario: Usuario) -> HTMLResponse:
    with ctx.container.uow() as uow:
        lista = uow.agendas.listar()
    nomes = {
        a.id_unidade_atendimento: a.unidade_atendimento or f"Unidade {a.id_unidade_atendimento}"
        for a in lista
        if a.id_unidade_atendimento is not None
    }
    return ctx.render(
        request,
        "admin/agendas.html",
        {
            "pagina": "agendas",
            "agendas": lista,
            "unidades": sorted(nomes.items(), key=lambda u: u[1]),
            "selecionadas": ctx.configuracao_atual().unidades,
        },
    )


@router.post("/unidades")
def salvar_unidades(
    request: Request,
    ctx: Ctx,
    _usuario: Usuario,
    _csrf: Csrf,
    modo: Annotated[str, Form()] = "todas",
    unidades: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    escolhidas = sorted(set(unidades or [])) if modo == "selecionadas" else []
    if modo == "selecionadas" and not escolhidas:
        mensagem(request, "Selecione ao menos uma unidade (ou escolha todas).", "erro")
    else:
        with ctx.container.uow() as uow:
            uow.configuracoes.definir(CHAVE_UNIDADES, ",".join(map(str, escolhidas)))
            uow.commit()
        mensagem(request, "Unidades do relatório atualizadas.")
    return redirecionar("/admin/agendas")


@router.post("/agendas/sincronizar")
def sincronizar_agendas(
    request: Request, ctx: Ctx, _usuario: Usuario, _csrf: Csrf
) -> RedirectResponse:
    try:
        r = ctx.container.casos.sincronizar.executar()
    except ErroIntegracao as erro:
        mensagem(request, f"SGG indisponível: {erro}", "erro")
    else:
        mensagem(
            request,
            f"Agendas sincronizadas: {r['agendas']} no SGG, {r['novas']} nova(s), "
            f"{r['desativadas']} desativada(s).",
        )
    return redirecionar("/admin/agendas")


@router.post("/agendas/{id_agenda}/incluir", response_class=HTMLResponse)
def alternar_agenda(
    request: Request,
    id_agenda: int,
    ctx: Ctx,
    _usuario: Usuario,
    _csrf: Csrf,
    incluir: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    with ctx.container.uow() as uow:
        uow.agendas.definir_inclusao(id_agenda, incluir)
        uow.commit()
        agenda = next((a for a in uow.agendas.listar() if a.id_agenda == id_agenda), None)
    if agenda is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    log.info("agenda_alterada", id_agenda=id_agenda, incluir=incluir)
    return ctx.render(request, "admin/_linha_agenda.html", {"a": agenda})


# ------------------------------------------------------------------ configurações


@router.get("/configuracoes", response_class=HTMLResponse)
def configuracoes(request: Request, ctx: Ctx, _usuario: Usuario) -> HTMLResponse:
    return ctx.render(
        request,
        "admin/configuracoes.html",
        {
            "pagina": "configuracoes",
            "config": ctx.configuracao_atual(),
            "padrao": ctx.settings.configuracao_padrao,
        },
    )


@router.post("/configuracoes")
def salvar_configuracoes(
    request: Request,
    ctx: Ctx,
    _usuario: Usuario,
    _csrf: Csrf,
    manha_inicio: Annotated[time, Form()],
    tarde_inicio: Annotated[time, Form()],
    tarde_fim: Annotated[time, Form()],
    atipico_min: Annotated[int, Form()],
    atipico_max: Annotated[int, Form()],
    email_tecnico: Annotated[str, Form()] = "",
) -> RedirectResponse:
    email_tecnico = email_tecnico.strip().lower()
    try:
        turnos = ConfiguracaoTurnos(manha_inicio, tarde_inicio, tarde_fim)
        RegrasMetricas(
            turnos=turnos,
            atipico_min=timedelta(minutes=atipico_min),
            atipico_max=timedelta(minutes=atipico_max),
        )
        if email_tecnico and not EMAIL_VALIDO.match(email_tecnico):
            raise ValueError("E-mail técnico inválido")
    except ValueError as erro:
        mensagem(request, str(erro), "erro")
        return redirecionar("/admin/configuracoes")
    valores = replace(
        ctx.configuracao_atual(),
        turnos=turnos,
        atipico_min_minutos=atipico_min,
        atipico_max_minutos=atipico_max,
        email_alerta_tecnico=email_tecnico or ctx.settings.email_alerta_tecnico,
    ).para_valores()
    chaves = (
        CHAVE_MANHA_INICIO,
        CHAVE_TARDE_INICIO,
        CHAVE_TARDE_FIM,
        CHAVE_ATIPICO_MIN,
        CHAVE_ATIPICO_MAX,
        CHAVE_EMAIL_TECNICO,
    )
    with ctx.container.uow() as uow:
        for chave in chaves:
            uow.configuracoes.definir(chave, valores[chave])
        uow.commit()
    log.info("configuracoes_alteradas", **{c: valores[c] for c in chaves})
    mensagem(
        request,
        "Configurações salvas. Valem para as próximas consolidações; "
        "reprocesse datas antigas se quiser recalculá-las.",
    )
    return redirecionar("/admin/configuracoes")


# ------------------------------------------------------------------ execuções


@router.get("/execucoes", response_class=HTMLResponse)
def execucoes(request: Request, ctx: Ctx, _usuario: Usuario) -> HTMLResponse:
    with ctx.container.uow() as uow:
        por_job = uow.execucoes.recentes_por_job(7)
    ordem = [j for j in JOBS if j in por_job] + sorted(j for j in por_job if j not in JOBS)
    return ctx.render(
        request,
        "admin/execucoes.html",
        {
            "pagina": "execucoes",
            "jobs": [(j, JOBS.get(j, j), por_job[j]) for j in ordem],
            "rotulos": ROTULOS_DETALHE,
        },
    )
