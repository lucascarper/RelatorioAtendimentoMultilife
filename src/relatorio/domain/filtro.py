"""Recorte do relatório por unidade de atendimento e agenda (RF07)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from relatorio.domain.entidades import Agenda
from relatorio.domain.metricas import AgendamentoDoDia


@dataclass(frozen=True, slots=True)
class FiltroRelatorio:
    """``unidades`` vazio significa "todas as unidades"."""

    unidades: frozenset[int] = frozenset()

    @property
    def todas_unidades(self) -> bool:
        return not self.unidades

    def inclui(self, agendamento: AgendamentoDoDia, agendas: Mapping[int, Agenda]) -> bool:
        agenda = agendas.get(agendamento.id_agenda) if agendamento.id_agenda is not None else None
        if agenda is not None and not agenda.incluir_relatorio:
            return False
        if self.todas_unidades:
            return True
        unidade = agendamento.id_unidade_atendimento
        if unidade is None and agenda is not None:
            unidade = agenda.id_unidade_atendimento
        return unidade in self.unidades

    def aplicar(
        self, agendamentos: Iterable[AgendamentoDoDia], agendas: Mapping[int, Agenda]
    ) -> list[AgendamentoDoDia]:
        return [a for a in agendamentos if self.inclui(a, agendas)]
