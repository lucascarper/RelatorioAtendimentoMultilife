"""Monitor em tempo real: resultado do caso de uso → valores prontos para a tela.

Reaproveita a apresentação do e-mail (mesmos cartões, turnos, consultórios, agendas
e alertas) e acrescenta o que só existe ao vivo: a fila de agora e o gráfico de
movimento por hora, desenhado em SVG no servidor (sem biblioteca de gráficos e sem
depender de JavaScript para exibir os números).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from relatorio.application.monitor import Monitor
from relatorio.domain.ao_vivo import MovimentoHora, SituacaoAoVivo
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.email.apresentacao import (
    Apresentacao,
    CartaoKpi,
    duracao,
    montar_apresentacao,
    numero,
)
from relatorio.interfaces.web.dependencias import SituacaoColeta

INTERVALO_ATUALIZACAO_S = 30

# Séries do gráfico (paleta validada: azul da marca + laranja, ΔE CVD 25,5).
SERIES = (
    ("chegadas", "Chegadas à recepção"),
    ("atendidos", "Atendimentos finalizados"),
)

# Geometria do gráfico (viewBox; o SVG escala com a largura do cartão).
LARGURA, ALTURA = 720, 236
MARGEM_ESQ, MARGEM_DIR, MARGEM_TOPO, MARGEM_BASE = 40, 8, 14, 30
LARGURA_BARRA_MAX = 20
VAO_BARRAS = 2
RAIO = 4


@dataclass(frozen=True, slots=True)
class CartaoAoVivo:
    rotulo: str
    valor: str
    contexto: str
    destaque: bool = False
    atencao: str = ""


@dataclass(frozen=True, slots=True)
class Coluna:
    serie: str
    caminho: str


@dataclass(frozen=True, slots=True)
class GrupoHora:
    rotulo: str
    x: float
    largura: float
    rotulo_x: float
    atual: bool
    futuro: bool
    colunas: tuple[Coluna, ...]
    dica: dict[str, Any]
    descricao: str
    chegadas: int
    atendidos: int


@dataclass(frozen=True, slots=True)
class Grafico:
    largura: int
    altura: int
    esquerda: float
    direita: float
    topo: float
    base: float
    marcas: tuple[tuple[float, str], ...]
    grupos: tuple[GrupoHora, ...]
    series: tuple[tuple[str, str], ...] = SERIES


@dataclass(frozen=True, slots=True)
class PainelMonitor:
    a: Apresentacao
    narrativa: str
    ao_vivo: tuple[CartaoAoVivo, ...]
    kpis: tuple[CartaoKpi, ...]
    consultorios: tuple[dict[str, Any], ...]
    comparacao: str
    grafico: Grafico
    total_chegadas: int
    total_atendidos: int
    atualizado_em: str
    dados_de: str
    coleta: str
    coleta_estado: str
    intervalo_s: int = INTERVALO_ATUALIZACAO_S


# ------------------------------------------------------------------ gráfico


def escala(maximo: int) -> tuple[int, tuple[int, ...]]:
    """Topo "redondo" do eixo e as marcas (no máximo 5, múltiplos de 1, 2 ou 5)."""
    if maximo <= 4:
        passo = 1
    else:
        bruto = maximo / 4
        magnitude = 10 ** math.floor(math.log10(bruto))
        passo = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= bruto)
    topo = max(passo, math.ceil(maximo / passo) * passo)
    return topo, tuple(range(0, topo + 1, passo))


def caminho_coluna(x: float, largura: float, y: float, base: float, raio: float = RAIO) -> str:
    """Coluna com a ponta arredondada e a base reta (vazia quando o valor é zero)."""
    altura = base - y
    if altura <= 0:
        return ""
    r = min(raio, altura, largura / 2)
    return (
        f"M{x:.1f},{base:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + largura - r:.1f}Q{x + largura:.1f},{y:.1f} {x + largura:.1f},{y + r:.1f}"
        f"V{base:.1f}Z"
    )


def montar_grafico(por_hora: tuple[MovimentoHora, ...], instante: datetime) -> Grafico:
    hora_atual = instante.astimezone(FUSO_BRASILIA).hour
    esquerda, direita = MARGEM_ESQ, LARGURA - MARGEM_DIR
    topo, base = MARGEM_TOPO, ALTURA - MARGEM_BASE
    maximo = max((max(h.chegadas, h.atendidos) for h in por_hora), default=0)
    teto, marcas = escala(maximo)

    def y(valor: int) -> float:
        return base - valor / teto * (base - topo)

    faixa = (direita - esquerda) / max(len(por_hora), 1)
    largura_barra = min(LARGURA_BARRA_MAX, (faixa - 12 - VAO_BARRAS) / 2)
    largura_grupo = 2 * largura_barra + VAO_BARRAS

    grupos = []
    for i, h in enumerate(por_hora):
        x_faixa = esquerda + i * faixa
        x_barra = x_faixa + (faixa - largura_grupo) / 2
        atual = h.hora == hora_atual
        futuro = h.hora > hora_atual
        intervalo = f"{h.hora:02d}h–{h.hora + 1:02d}h"
        titulo = intervalo + (" · em andamento" if atual else "")
        colunas = tuple(
            Coluna(
                serie,
                caminho_coluna(
                    x_barra + j * (largura_barra + VAO_BARRAS), largura_barra, y(valor), base
                ),
            )
            for j, (serie, valor) in enumerate(
                (("chegadas", h.chegadas), ("atendidos", h.atendidos))
            )
        )
        grupos.append(
            GrupoHora(
                rotulo=f"{h.hora:02d}h",
                x=x_faixa,
                largura=faixa,
                rotulo_x=x_faixa + faixa / 2,
                atual=atual,
                futuro=futuro,
                colunas=colunas,
                dica={
                    "titulo": titulo,
                    "linhas": [
                        ["chegadas", SERIES[0][1], numero(h.chegadas)],
                        ["atendidos", SERIES[1][1], numero(h.atendidos)],
                    ],
                },
                descricao=(
                    f"{titulo}: {h.chegadas} chegadas à recepção, "
                    f"{h.atendidos} atendimentos finalizados"
                ),
                chegadas=h.chegadas,
                atendidos=h.atendidos,
            )
        )
    return Grafico(
        largura=LARGURA,
        altura=ALTURA,
        esquerda=esquerda,
        direita=direita,
        topo=topo,
        base=base,
        marcas=tuple((y(m), numero(m)) for m in marcas),
        grupos=tuple(grupos),
    )


# ------------------------------------------------------------------ fila de agora


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{numero(n)} {singular if n == 1 else plural}"


def cartoes_ao_vivo(v: SituacaoAoVivo) -> tuple[CartaoAoVivo, ...]:
    return (
        CartaoAoVivo(
            rotulo="Na recepção agora",
            valor=numero(v.aguardando),
            contexto=(
                f"maior espera {duracao(v.espera_atual_maxima_s)} · "
                f"média {duracao(v.espera_atual_media_s)}"
                if v.aguardando
                else "ninguém aguardando atendimento"
            ),
            destaque=True,
        ),
        CartaoAoVivo(
            rotulo="Em atendimento",
            valor=numero(v.em_atendimento),
            contexto=(
                f"o mais longo começou há {duracao(v.atendimento_atual_maximo_s)}"
                if v.em_atendimento
                else "nenhum atendimento em andamento"
            ),
        ),
        CartaoAoVivo(
            rotulo="Ainda não chegaram",
            valor=numero(v.a_chegar),
            contexto="agendados de hoje sem chegada registrada",
            atencao=(
                f"{numero(v.a_chegar_atrasados)} com horário vencido há mais de 15 min"
                if v.a_chegar_atrasados
                else ""
            ),
        ),
    )


def narrativa(v: SituacaoAoVivo, a: Apresentacao) -> str:
    if v.aguardando:
        agora = (
            f"{_plural(v.aguardando, 'pessoa', 'pessoas')} na recepção agora; "
            f"a maior espera é de {duracao(v.espera_atual_maxima_s)}."
        )
    else:
        agora = "Ninguém aguardando na recepção agora."
    if a.sem_movimento:
        return f"{agora} Ainda não há agendamentos hoje nas unidades do relatório."
    return f"{agora} Hoje até agora: {a.manchete[0].lower()}{a.manchete[1:]}"


# ------------------------------------------------------------------ painel


def _json_ao_vivo(m: Monitor) -> dict[str, Any]:
    """JSON do resumo com os alertas de fim de dia desligados.

    "Sem baixa" e "em atendimento" são alertas quando o dia acabou; durante o dia são
    a fila normal — aparecem nos cartões de agora, não como alerta.
    """
    dados = m.resumo.para_json()
    alertas = dict(dados["alertas"])
    for chave in ("sem_baixa", "em_atendimento_aberto"):
        alertas[chave] = {"total": 0, "ids": []}
    dados["alertas"] = alertas
    return dados


def _estado_coleta(coleta: SituacaoColeta, coletor_habilitado: bool) -> tuple[str, str, str]:
    """(texto, estado, horário dos dados) da coleta que alimenta o monitor."""
    if not coletor_habilitado:
        return "Eventos gravados pelo coletor do painel em tempo real", "neutro", "—"
    if coleta.ultima is None:
        return "Nenhum ciclo de coleta ainda", "erro", "—"
    dados_de = coleta.ultima.inicio.astimezone(FUSO_BRASILIA).strftime("%H:%M:%S")
    if coleta.atrasada:
        return "Coleta atrasada — os números podem estar desatualizados", "erro", dados_de
    if coleta.em_coleta:
        return "Coletando do SGG a cada minuto", "ok", dados_de
    return "Fora do horário de coleta", "neutro", dados_de


def montar_painel(m: Monitor, coleta: SituacaoColeta, coletor_habilitado: bool) -> PainelMonitor:
    dados = _json_ao_vivo(m)
    a = montar_apresentacao(dados)
    v = m.ao_vivo
    ate = m.instante_base.strftime("%H:%M")
    kpis = tuple(
        replace(k, delta=replace(k.delta, rotulo=f"{k.delta.rotulo} até {ate}")) if k.delta else k
        for k in a.kpis
    )
    texto, estado, dados_de = _estado_coleta(coleta, coletor_habilitado)
    return PainelMonitor(
        a=a,
        narrativa=narrativa(v, a),
        ao_vivo=cartoes_ao_vivo(v),
        kpis=kpis,
        consultorios=tuple(
            {**linha, "atendimentos": numero(bruto["total"]["atendimentos"])}
            for linha, bruto in zip(a.consultorios, dados["consultorios"], strict=True)
        ),
        comparacao=(
            f"comparado com {a.comparativo_base} até {ate}"
            if a.comparativo_disponivel
            else "sem base de comparação na semana anterior"
        ),
        grafico=montar_grafico(v.por_hora, v.instante),
        total_chegadas=sum(h.chegadas for h in v.por_hora),
        total_atendidos=sum(h.atendidos for h in v.por_hora),
        atualizado_em=v.instante.astimezone(FUSO_BRASILIA).strftime("%H:%M:%S"),
        dados_de=dados_de,
        coleta=texto,
        coleta_estado=estado,
    )
