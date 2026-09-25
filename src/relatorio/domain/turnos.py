"""Turnos de atendimento (configuráveis).

Padrão confirmado: Manhã = 06:00 a 12:59; Tarde = 13:00 a 18:00. O turno é definido
pela hora *agendada*: um agendamento é da tarde quando a hora agendada for igual ou
posterior ao início da tarde. Horários fora da janela caem no turno mais próximo
(antes das 06:00 → manhã; depois das 18:00 → tarde), para que nenhum agendamento
fique sem turno por causa de um encaixe fora do horário.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from relatorio.domain.entidades import Turno


def _menos_um_minuto(hora: time) -> time:
    minutos = (hora.hour * 60 + hora.minute - 1) % (24 * 60)
    return time(minutos // 60, minutos % 60)


@dataclass(frozen=True, slots=True)
class ConfiguracaoTurnos:
    manha_inicio: time = time(6, 0)
    tarde_inicio: time = time(13, 0)
    tarde_fim: time = time(18, 0)

    def __post_init__(self) -> None:
        if not self.manha_inicio < self.tarde_inicio <= self.tarde_fim:
            raise ValueError(
                "Turnos inválidos: é preciso que início da manhã < início da tarde ≤ fim da tarde"
            )

    def classificar(self, hora: time) -> Turno:
        return Turno.TARDE if hora >= self.tarde_inicio else Turno.MANHA

    @property
    def manha_fim(self) -> time:
        return _menos_um_minuto(self.tarde_inicio)

    def faixa(self, turno: Turno) -> str:
        if turno is Turno.MANHA:
            inicio, fim = self.manha_inicio, self.manha_fim
        else:
            inicio, fim = self.tarde_inicio, self.tarde_fim
        return f"{inicio:%H:%M} às {fim:%H:%M}"
