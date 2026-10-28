"""Dashboard do atendimento: os resumos diários de um período somados num só.

Usa só o que já foi consolidado (``resumo_diario.metricas``): os números batem com os
e-mails de cada dia. Contagens são somadas; a taxa de faltas é recalculada pelas somas;
os tempos médios são médias ponderadas pelo volume de cada dia (nunca média das médias).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from relatorio.domain.entidades import Turno
from relatorio.domain.exportacao import dias
from relatorio.domain.metricas import Kpis, ResumoTurno


@dataclass(frozen=True, slots=True)
class DiaGrafico:
    data: date
    atendimentos: int | None  # None: dia sem resumo ou sem movimento (lacuna no gráfico)
    faltas: int | None


@dataclass(frozen=True, slots=True)
class ResumoPeriodo:
    inicio: date
    fim: date
    dias_consolidados: int
    ultimo_dia: date | None
    kpis: Kpis
    alertas: int
    por_turno: tuple[ResumoTurno, ...]
    dias: tuple[DiaGrafico, ...]

    @property
    def vazio(self) -> bool:
        return self.dias_consolidados == 0


def _media_ponderada(pares: Iterable[tuple[int | None, int]]) -> int | None:
    validos = [(valor, peso) for valor, peso in pares if valor is not None and peso > 0]
    total = sum(peso for _, peso in validos)
    return round(sum(valor * peso for valor, peso in validos) / total) if total else None


def _soma(valores: Iterable[int | None]) -> int | None:
    """Soma dos disponíveis; None se nenhum dia tem o valor (ex.: sem guichês marcados)."""
    presentes = [v for v in valores if v is not None]
    return sum(presentes) if presentes else None


def _taxa(parte: int, todo: int) -> float | None:
    return parte / todo if todo else None


def _somar_kpis(diarios: list[Kpis]) -> Kpis:
    # shortcut: a quantidade de esperas medidas não é gravada no resumo; as esperas e os
    # tempos por grupo são ponderados pelos atendimentos do dia. Gravar as contagens se
    # a diferença para o cálculo direto do período passar a importar.
    def ponderar(chave: str, peso: str) -> int | None:
        return _media_ponderada((getattr(k, chave), getattr(k, peso) or 0) for k in diarios)

    agendados = sum(k.agendados for k in diarios)
    faltas = sum(k.faltas for k in diarios)
    esperas = [k.espera_maxima_s for k in diarios if k.espera_maxima_s is not None]
    partes = (
        ponderar("espera_recepcao_s", "atendimentos_guiches"),
        ponderar("tma_guiches_s", "atendimentos_guiches"),
        ponderar("espera_consultorio_s", "atendimentos_consultorios"),
        ponderar("tma_consultorios_s", "atendimentos_consultorios"),
    )
    return Kpis(
        agendados=agendados,
        cancelados=sum(k.cancelados for k in diarios),
        atendimentos=sum(k.atendimentos for k in diarios),
        faltas=faltas,
        taxa_faltas=_taxa(faltas, agendados),
        sem_baixa=sum(k.sem_baixa for k in diarios),
        espera_media_s=ponderar("espera_media_s", "atendimentos"),
        espera_mediana_s=None,  # a mediana do período não sai das medianas dos dias
        espera_maxima_s=max(esperas) if esperas else None,
        tma_s=ponderar("tma_s", "atendimentos_medidos"),
        atendimentos_medidos=sum(k.atendimentos_medidos for k in diarios),
        sem_turno=sum(k.sem_turno for k in diarios),
        atendimentos_consultorios=_soma(k.atendimentos_consultorios for k in diarios),
        atendimentos_guiches=_soma(k.atendimentos_guiches for k in diarios),
        espera_consultorio_s=partes[2],
        espera_recepcao_s=partes[0],
        tma_consultorios_s=partes[3],
        tma_guiches_s=partes[1],
        # Mesma definição do dia: soma das quatro médias (só com guichês marcados).
        permanencia_total_s=None if None in partes else sum(p for p in partes if p is not None),
    )


def _somar_turno(turno: Turno, diarios: list[Mapping[str, Any]]) -> ResumoTurno:
    def inteiro(dado: Mapping[str, Any], chave: str) -> int:
        valor = dado.get(chave)
        return int(valor) if isinstance(valor, int | float) else 0

    agendados = sum(inteiro(t, "agendados") for t in diarios)
    faltas = sum(inteiro(t, "faltas") for t in diarios)
    esperas = [t["espera_maxima_s"] for t in diarios if t.get("espera_maxima_s") is not None]
    return ResumoTurno(
        rotulo=turno.rotulo,
        faixa=next((str(t["faixa"]) for t in reversed(diarios) if t.get("faixa")), ""),
        agendados=agendados,
        atendimentos=sum(inteiro(t, "atendimentos") for t in diarios),
        faltas=faltas,
        taxa_faltas=_taxa(faltas, agendados),
        espera_media_s=_media_ponderada(
            (t.get("espera_media_s"), inteiro(t, "atendimentos")) for t in diarios
        ),
        espera_mediana_s=None,
        espera_maxima_s=max(esperas) if esperas else None,
        tma_s=_media_ponderada(
            (t.get("tma_s"), inteiro(t, "atendimentos_medidos")) for t in diarios
        ),
        atendimentos_medidos=sum(inteiro(t, "atendimentos_medidos") for t in diarios),
    )


def resumir_periodo(
    inicio: date, fim: date, resumos: Iterable[tuple[date, Mapping[str, Any]]]
) -> ResumoPeriodo:
    """Soma os resumos diários (data, métricas gravadas) de ``inicio`` a ``fim``."""
    do_periodo = sorted((d, m) for d, m in resumos if inicio <= d <= fim)
    kpis_por_dia = {d: Kpis.de_dict(m.get("kpis", {})) for d, m in do_periodo}
    com_movimento = {d: k for d, k in kpis_por_dia.items() if k.agendados > 0}
    return ResumoPeriodo(
        inicio=inicio,
        fim=fim,
        dias_consolidados=len(do_periodo),
        ultimo_dia=do_periodo[-1][0] if do_periodo else None,
        kpis=_somar_kpis(list(kpis_por_dia.values())),
        alertas=sum(int(m.get("alertas", {}).get("quantidade", 0)) for _, m in do_periodo),
        por_turno=tuple(
            _somar_turno(
                turno,
                [
                    m["por_turno"][turno.value]
                    for _, m in do_periodo
                    if turno.value in m.get("por_turno", {})
                ],
            )
            for turno in Turno
        ),
        dias=tuple(
            DiaGrafico(
                data=dia,
                atendimentos=com_movimento[dia].atendimentos if dia in com_movimento else None,
                faltas=com_movimento[dia].faltas if dia in com_movimento else None,
            )
            for dia in dias(inicio, fim)
        ),
    )
