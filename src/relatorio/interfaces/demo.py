"""Demonstração: simula dias completos pelo mesmo caminho da produção.

Sincroniza agendas (05:30), faz o polling minuto a minuto (06:00–18:00, com uma janela
de indisponibilidade opcional), reconcilia (18:30) e consolida (23:00) — tudo com um
SGG simulado e um relógio controlado. Serve para a prévia do e-mail, para popular o
banco local (docker compose) e para o teste ponta a ponta. Dados 100% fictícios.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.application.modelos import ConteudoEmail
from relatorio.application.montagem import CasosDeUso, montar_casos_de_uso
from relatorio.application.ports import FabricaUoW
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.email.renderizador import LOGO_CID, RenderizadorJinja
from relatorio.infrastructure.jobs import ExecutorJobs
from relatorio.infrastructure.memoria import BancoEmMemoria, UoWEmMemoria
from relatorio.infrastructure.sgg.simulado import AgendamentoSimulado, SggSimulado, gerar_dia

FALHA_PADRAO = (time(10, 12), time(10, 40))


@dataclass
class RelogioAjustavel:
    instante: datetime

    def agora(self) -> datetime:
        return self.instante


@dataclass
class EmailEmMemoria:
    enviados: list[tuple[list[str], ConteudoEmail]] = field(default_factory=list)

    def enviar(self, destinatarios: Sequence[str], conteudo: ConteudoEmail) -> None:
        self.enviados.append((list(destinatarios), conteudo))


def _as(dia: date, hora: time) -> datetime:
    return datetime.combine(dia, hora, tzinfo=FUSO_BRASILIA)


def janelas_de_falha(
    dias: Sequence[date], falha: tuple[time, time] | None
) -> list[tuple[datetime, datetime]]:
    if falha is None:
        return []
    return [(_as(dia, falha[0]), _as(dia, falha[1])) for dia in dias]


def simular_dias(
    dias: Sequence[date],
    casos: CasosDeUso,
    executor: ExecutorJobs,
    relogio: RelogioAjustavel,
    janela: JanelaColeta | None = None,
) -> None:
    janela = janela or JanelaColeta()
    for dia in dias:
        relogio.instante = _as(dia, time(5, 30))
        executor.executar("sync_agendas", casos.sincronizar.executar)
        instante, fim = janela.do_dia(dia)
        while instante <= fim:
            relogio.instante = instante
            executor.executar(
                "coletar_ciclo",
                casos.coletar.executar,
                trava="coletor",
                ao_falhar=casos.alerta_coleta.verificar,
            )
            instante += timedelta(minutes=1)
        relogio.instante = _as(dia, time(18, 30))
        executor.executar(
            "reconciliar_dia", partial(casos.reconciliar.executar, dia), trava="coletor"
        )
        relogio.instante = _as(dia, time(23, 0))
        executor.executar("consolidar_dia", partial(casos.consolidar.executar_agendado, dia, 1))


def volume_do_dia(dia: date, volume: int, ultimo: bool) -> int:
    """Domingo sem expediente (exercita o RF09), sábado reduzido, dias úteis variando."""
    if dia.weekday() == 6:
        return 0
    base = volume if ultimo else volume - 6 - dia.toordinal() % 9
    return base * 2 // 5 if dia.weekday() == 5 else base


@dataclass
class AmbienteDemo:
    casos: CasosDeUso
    executor: ExecutorJobs
    relogio: RelogioAjustavel
    sgg: SggSimulado
    email: EmailEmMemoria
    agendamentos: list[AgendamentoSimulado]


def montar_ambiente(
    dias: Sequence[date],
    uow: FabricaUoW,
    renderizador: RenderizadorJinja | Any,
    *,
    falha: tuple[time, time] | None = FALHA_PADRAO,
    volume: int = 120,
    engine: Any = None,
    configuracao: ConfiguracaoRelatorio | None = None,
) -> AmbienteDemo:
    relogio = RelogioAjustavel(_as(dias[0], time(5, 0)))
    agendamentos = [
        ag
        for indice, dia in enumerate(dias)
        for ag in gerar_dia(dia, 7 + indice, volume_do_dia(dia, volume, indice == len(dias) - 1))
    ]
    sgg = SggSimulado(agendamentos, relogio.agora, janelas_de_falha(dias, falha))
    email = EmailEmMemoria()
    casos = montar_casos_de_uso(
        uow=uow,
        relogio=relogio,
        sgg=sgg,
        email=email,
        renderizador=renderizador,
        configuracao_padrao=configuracao or ConfiguracaoRelatorio(),
        janela=JanelaColeta(),
    )
    return AmbienteDemo(
        casos=casos,
        executor=ExecutorJobs(engine, uow, relogio),
        relogio=relogio,
        sgg=sgg,
        email=email,
        agendamentos=agendamentos,
    )


def gerar_previa(
    dia: date,
    templates_dir: Path,
    static_dir: Path,
    admin_url: str = "",
    falha: tuple[time, time] | None = FALHA_PADRAO,
) -> tuple[dict[str, Any], ConteudoEmail]:
    """Simula a semana anterior e o dia, consolida e renderiza o e-mail (dados fictícios)."""
    banco = BancoEmMemoria()
    renderizador = RenderizadorJinja(templates_dir, static_dir, admin_url)
    dias = [dia - timedelta(days=7), dia]
    ambiente = montar_ambiente(dias, lambda: UoWEmMemoria(banco), renderizador, falha=falha)
    simular_dias(dias, ambiente.casos, ambiente.executor, ambiente.relogio)
    metricas = dict(banco.resumos[dia].metricas)
    metricas["amostra"] = True
    return metricas, renderizador.relatorio(metricas)


def html_para_navegador(conteudo: ConteudoEmail) -> str:
    """Troca ``cid:`` por data URI para abrir o e-mail direto no navegador."""
    html = conteudo.html
    for imagem in conteudo.imagens:
        dados = base64.b64encode(imagem.conteudo).decode()
        html = html.replace(f"cid:{imagem.cid}", f"data:image/{imagem.subtipo};base64,{dados}")
    return html.replace(f"cid:{LOGO_CID}", "")
