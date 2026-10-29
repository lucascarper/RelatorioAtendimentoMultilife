"""Dashboard do SESMT: os resumos diários de um período num só.

Do dia (somados no período): eventos do eSocial, com e sem recibo, empresas e eventos por
tipo. Foto (vale o último dia com resumo): documentos e contratos a vencer e vencidos.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from relatorio.domain.dashboard import numero_em
from relatorio.domain.exportacao import dias

_PENDENCIAS = ("total", "vencendo", "contratos", "documentos")
CAMPOS = (
    *(("esocial", c) for c in ("total", "com_recibo", "sem_recibo", "empresas", "por_evento")),
    *(("a_vencer", c) for c in _PENDENCIAS),
    *(("vencidos", c) for c in _PENDENCIAS if c != "vencendo"),
)


@dataclass(frozen=True, slots=True)
class DiaSesmt:
    data: date
    com_recibo: int | None  # None: dia sem resumo (lacuna nos gráficos)
    sem_recibo: int | None
    a_vencer: int | None
    vencidos: int | None


@dataclass(frozen=True, slots=True)
class FotoSesmt:
    data: date
    a_vencer: int
    vencendo: int
    a_vencer_contratos: int
    a_vencer_documentos: int
    vencidos: int
    vencidos_contratos: int
    vencidos_documentos: int


@dataclass(frozen=True, slots=True)
class PeriodoSesmt:
    inicio: date
    fim: date
    dias_consolidados: int
    eventos: int
    com_recibo: int
    sem_recibo: int
    empresas: int  # somadas dia a dia: a mesma empresa conta em cada dia com evento
    por_evento: tuple[tuple[str, int], ...]
    foto: FotoSesmt | None
    dias: tuple[DiaSesmt, ...]

    @property
    def vazio(self) -> bool:
        return self.dias_consolidados == 0

    @property
    def ultimo_dia(self) -> date | None:
        return self.foto.data if self.foto else None


def _inteiro(m: Any, *caminho: str) -> int:
    return int(numero_em(m, *caminho) or 0)


def _foto(dia: date, m: Mapping[str, Any]) -> FotoSesmt:
    return FotoSesmt(
        data=dia,
        a_vencer=_inteiro(m, "a_vencer", "total"),
        vencendo=_inteiro(m, "a_vencer", "vencendo"),
        a_vencer_contratos=_inteiro(m, "a_vencer", "contratos"),
        a_vencer_documentos=_inteiro(m, "a_vencer", "documentos"),
        vencidos=_inteiro(m, "vencidos", "total"),
        vencidos_contratos=_inteiro(m, "vencidos", "contratos"),
        vencidos_documentos=_inteiro(m, "vencidos", "documentos"),
    )


def resumir_sesmt(
    inicio: date, fim: date, resumos: Iterable[tuple[date, Mapping[str, Any]]]
) -> PeriodoSesmt:
    """Soma os resumos diários (data, métricas gravadas) de ``inicio`` a ``fim``."""
    por_dia = dict(sorted(((d, m) for d, m in resumos if inicio <= d <= fim), key=lambda x: x[0]))
    por_evento: Counter[str] = Counter()
    for m in por_dia.values():
        tipos = (m.get("esocial") or {}).get("por_evento") or {}
        por_evento.update({str(t): int(n) for t, n in tipos.items() if isinstance(n, int)})
    ultimo = next(reversed(por_dia.items()), None)
    return PeriodoSesmt(
        inicio=inicio,
        fim=fim,
        dias_consolidados=len(por_dia),
        eventos=sum(_inteiro(m, "esocial", "total") for m in por_dia.values()),
        com_recibo=sum(_inteiro(m, "esocial", "com_recibo") for m in por_dia.values()),
        sem_recibo=sum(_inteiro(m, "esocial", "sem_recibo") for m in por_dia.values()),
        empresas=sum(_inteiro(m, "esocial", "empresas") for m in por_dia.values()),
        por_evento=tuple(sorted(por_evento.items(), key=lambda par: (-par[1], par[0]))),
        foto=_foto(*ultimo) if ultimo else None,
        dias=tuple(
            DiaSesmt(
                data=dia,
                com_recibo=_inteiro(m, "esocial", "com_recibo") if m is not None else None,
                sem_recibo=_inteiro(m, "esocial", "sem_recibo") if m is not None else None,
                a_vencer=_inteiro(m, "a_vencer", "total") if m is not None else None,
                vencidos=_inteiro(m, "vencidos", "total") if m is not None else None,
            )
            for dia, m in ((d, por_dia.get(d)) for d in dias(inicio, fim))
        ),
    )
