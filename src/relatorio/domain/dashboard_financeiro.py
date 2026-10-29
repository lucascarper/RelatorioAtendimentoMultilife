"""Dashboard do financeiro: os resumos diários de um período num só.

Do dia (somados no período): caixa recebido e pago, e faturamento. Foto (vale o último dia
com resumo): MRR, contratos ativos e contratos a vencer. Os dados "mês até a data"
(categorias, margem, rateio) ficam de fora: só fecham para um mês a partir do dia 1º.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from relatorio.domain.dashboard import numero_em
from relatorio.domain.exportacao import dias

CAMPOS = (
    ("caixa",),
    ("faturamento", "dia"),
    ("faturamento", "titulos_dia"),
    ("recorrente",),
    ("contratos_a_vencer",),
)


@dataclass(frozen=True, slots=True)
class DiaFinanceiro:
    data: date
    recebido: float | None  # None: dia sem resumo (lacuna no gráfico)
    pago: float | None
    saldo_acumulado: float | None


@dataclass(frozen=True, slots=True)
class FotoFinanceira:
    data: date
    mrr: float | None
    clientes_mrr: int
    contratos_ativos: int | None
    contratos_a_vencer: int
    mensalidades_a_vencer: float


@dataclass(frozen=True, slots=True)
class PeriodoFinanceiro:
    inicio: date
    fim: date
    dias_consolidados: int
    recebido: float
    pago: float
    titulos_recebidos: int
    titulos_pagos: int
    faturamento: float
    titulos_faturados: int
    foto: FotoFinanceira | None
    dias: tuple[DiaFinanceiro, ...]

    @property
    def vazio(self) -> bool:
        return self.dias_consolidados == 0

    @property
    def ultimo_dia(self) -> date | None:
        return self.foto.data if self.foto else None

    @property
    def saldo(self) -> float:
        return round(self.recebido - self.pago, 2)


def _foto(dia: date, m: Mapping[str, Any]) -> FotoFinanceira:
    contratos = [c for c in m.get("contratos_a_vencer") or [] if isinstance(c, Mapping)]
    ativos = numero_em(m, "recorrente", "contratos_ativos")
    return FotoFinanceira(
        data=dia,
        mrr=numero_em(m, "recorrente", "mrr"),
        clientes_mrr=int(numero_em(m, "recorrente", "clientes_mrr") or 0),
        contratos_ativos=int(ativos) if ativos is not None else None,
        contratos_a_vencer=len(contratos),
        mensalidades_a_vencer=round(sum(numero_em(c, "mensalidade") or 0 for c in contratos), 2),
    )


def resumir_financeiro(
    inicio: date, fim: date, resumos: Iterable[tuple[date, Mapping[str, Any]]]
) -> PeriodoFinanceiro:
    """Soma os resumos diários (data, métricas gravadas) de ``inicio`` a ``fim``."""
    por_dia = dict(sorted(((d, m) for d, m in resumos if inicio <= d <= fim), key=lambda x: x[0]))

    def soma(*caminho: str) -> float:
        return sum(numero_em(m, *caminho) or 0 for m in por_dia.values())

    serie, acumulado = [], 0.0
    for dia in dias(inicio, fim):
        m = por_dia.get(dia)
        if m is None:
            serie.append(DiaFinanceiro(dia, None, None, None))
            continue
        recebido = numero_em(m, "caixa", "recebido_dia") or 0.0
        pago = numero_em(m, "caixa", "pago_dia") or 0.0
        acumulado += recebido - pago
        serie.append(DiaFinanceiro(dia, recebido, pago, round(acumulado, 2)))
    ultimo = next(reversed(por_dia.items()), None)
    return PeriodoFinanceiro(
        inicio=inicio,
        fim=fim,
        dias_consolidados=len(por_dia),
        recebido=round(soma("caixa", "recebido_dia"), 2),
        pago=round(soma("caixa", "pago_dia"), 2),
        titulos_recebidos=int(soma("caixa", "titulos_recebidos_dia")),
        titulos_pagos=int(soma("caixa", "titulos_pagos_dia")),
        faturamento=round(soma("faturamento", "dia"), 2),
        titulos_faturados=int(soma("faturamento", "titulos_dia")),
        foto=_foto(*ultimo) if ultimo else None,
        dias=tuple(serie),
    )
