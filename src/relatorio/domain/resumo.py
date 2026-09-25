"""Resumo diário: o JSON gravado em ``resumo_diario.metricas``.

O template do e-mail recebe só este JSON — nenhuma regra de cálculo fica no template.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any

from relatorio.domain.comparativo import Comparativo
from relatorio.domain.metricas import FalhaColeta, MetricasPeriodo

DIAS_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def dia_semana(data: date) -> str:
    return DIAS_SEMANA[data.weekday()]


def assunto_email(data: date) -> str:
    """Ex.: ``Resumo de Atendimentos — 23/09/2026 (terça-feira)``."""
    return f"Resumo de Atendimentos — {data:%d/%m/%Y} ({dia_semana(data)})"


def para_json(valor: Any) -> Any:  # noqa: PLR0911 — um retorno por tipo suportado
    """Converte estruturas do domínio em tipos aceitos por JSON."""
    if isinstance(valor, FalhaColeta):
        return {
            "inicio": valor.inicio.isoformat(),
            "fim": valor.fim.isoformat(),
            "minutos": valor.minutos,
        }
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        return {f.name: para_json(getattr(valor, f.name)) for f in dataclasses.fields(valor)}
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, datetime | date):
        return valor.isoformat()
    if isinstance(valor, time):
        return valor.strftime("%H:%M")
    if isinstance(valor, timedelta):
        return round(valor.total_seconds())
    if isinstance(valor, Mapping):
        return {str(k): para_json(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple | set | frozenset):
        return [para_json(v) for v in valor]
    return valor


@dataclass(frozen=True, slots=True)
class ResumoDiario:
    data_referencia: date
    unidades: tuple[str, ...]
    gerado_em: datetime
    metricas: MetricasPeriodo
    comparativo: Comparativo

    @property
    def sem_movimento(self) -> bool:
        return self.metricas.sem_movimento

    def para_json(self) -> dict[str, Any]:
        m = self.metricas
        alertas = para_json(m.alertas)
        alertas["quantidade"] = m.alertas.quantidade
        return {
            "versao_regra": m.versao_regra,
            "data_referencia": self.data_referencia.isoformat(),
            "dia_semana": dia_semana(self.data_referencia),
            "assunto": assunto_email(self.data_referencia),
            "gerado_em": self.gerado_em.isoformat(),
            "unidades": list(self.unidades),
            "sem_movimento": self.sem_movimento,
            "periodo": {"inicio": m.inicio.isoformat(), "fim": m.fim.isoformat()},
            "limites_atipico": {
                "min_minutos": m.limites_atipico_minutos[0],
                "max_minutos": m.limites_atipico_minutos[1],
            },
            "kpis": para_json(m.kpis),
            "por_turno": para_json(m.por_turno),
            "consultorios": para_json(m.consultorios),
            "agendas": para_json(m.agendas),
            "por_turno_grupos": para_json(m.por_turno_grupos),
            "consultorios_por_turno": para_json(m.consultorios_por_turno),
            "agendas_por_turno": para_json(m.agendas_por_turno),
            "comparativo": para_json(self.comparativo),
            "alertas": alertas,
        }
