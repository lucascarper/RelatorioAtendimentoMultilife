"""Comparativo contra o mesmo dia da semana anterior (seção 12, item 6).

Boas práticas de leitura: taxas variam em pontos percentuais (p.p.), contagens e tempos
em variação percentual. Cada indicador sabe se subir é bom, ruim ou neutro — o e-mail
só pinta de verde/vermelho quando essa leitura é inequívoca (o TMA, por exemplo, é
neutro: atendimento mais longo não é necessariamente pior).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from relatorio.domain.metricas import Kpis


class Sentido(StrEnum):
    MAIOR_MELHOR = "maior_melhor"
    MENOR_MELHOR = "menor_melhor"
    NEUTRO = "neutro"


class Formato(StrEnum):
    NUMERO = "numero"
    PERCENTUAL = "percentual"
    DURACAO = "duracao"


@dataclass(frozen=True, slots=True)
class ItemComparativo:
    chave: str
    rotulo: str
    formato: Formato
    atual: float | None
    anterior: float | None
    variacao: float | None
    em_pontos: bool
    direcao: str | None  # "alta" | "baixa" | "estavel"
    avaliacao: str | None  # "positiva" | "negativa" | "neutra"


@dataclass(frozen=True, slots=True)
class Comparativo:
    data_base: date
    disponivel: bool
    itens: tuple[ItemComparativo, ...]

    def item(self, chave: str) -> ItemComparativo | None:
        return next((i for i in self.itens if i.chave == chave), None)


@dataclass(frozen=True, slots=True)
class _Indicador:
    chave: str
    rotulo: str
    formato: Formato
    sentido: Sentido
    em_pontos: bool = False


_INDICADORES: tuple[_Indicador, ...] = (
    _Indicador("atendimentos", "Atendimentos", Formato.NUMERO, Sentido.MAIOR_MELHOR),
    _Indicador("faltas", "Faltas", Formato.NUMERO, Sentido.MENOR_MELHOR),
    _Indicador("taxa_faltas", "Taxa de faltas", Formato.PERCENTUAL, Sentido.MENOR_MELHOR, True),
    _Indicador("espera_media_s", "Espera média", Formato.DURACAO, Sentido.MENOR_MELHOR),
    _Indicador("tma_s", "TMA geral", Formato.DURACAO, Sentido.NEUTRO),
    _Indicador("agendados", "Agendados", Formato.NUMERO, Sentido.NEUTRO),
)

# Abaixo disso a variação é tratada como estabilidade (evita setas por ruído).
LIMIAR_ESTAVEL_PERCENTUAL = 0.005
LIMIAR_ESTAVEL_PONTOS = 0.001


def _valor(kpis: Kpis, chave: str) -> float | None:
    valor = getattr(kpis, chave)
    return float(valor) if valor is not None else None


def _comparar_item(
    indicador: _Indicador, atual: float | None, anterior: float | None
) -> ItemComparativo:
    em_pontos, sentido = indicador.em_pontos, indicador.sentido
    variacao: float | None = None
    if atual is not None and anterior is not None:
        if em_pontos:
            variacao = atual - anterior
        elif anterior != 0:
            variacao = (atual - anterior) / anterior

    direcao: str | None = None
    avaliacao: str | None = None
    if variacao is not None:
        limiar = LIMIAR_ESTAVEL_PONTOS if em_pontos else LIMIAR_ESTAVEL_PERCENTUAL
        if abs(variacao) < limiar:
            direcao, avaliacao = "estavel", "neutra"
        else:
            direcao = "alta" if variacao > 0 else "baixa"
            if sentido is Sentido.NEUTRO:
                avaliacao = "neutra"
            else:
                melhorou = (direcao == "alta") == (sentido is Sentido.MAIOR_MELHOR)
                avaliacao = "positiva" if melhorou else "negativa"

    return ItemComparativo(
        chave=indicador.chave,
        rotulo=indicador.rotulo,
        formato=indicador.formato,
        atual=atual,
        anterior=anterior,
        variacao=variacao,
        em_pontos=em_pontos,
        direcao=direcao,
        avaliacao=avaliacao,
    )


def comparar(atual: Kpis, anterior: Kpis | None, data_base: date) -> Comparativo:
    itens = tuple(
        _comparar_item(
            indicador,
            _valor(atual, indicador.chave),
            _valor(anterior, indicador.chave) if anterior is not None else None,
        )
        for indicador in _INDICADORES
    )
    return Comparativo(data_base=data_base, disponivel=anterior is not None, itens=itens)
