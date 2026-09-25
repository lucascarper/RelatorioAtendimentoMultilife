"""Configurações de negócio editáveis no admin (tabela ``configuracao``).

Os valores padrão vêm das variáveis de ambiente; o que for salvo no admin prevalece.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.filtro import FiltroRelatorio
from relatorio.domain.metricas import RegrasMetricas
from relatorio.domain.turnos import ConfiguracaoTurnos

CHAVE_MANHA_INICIO = "turno_manha_inicio"
CHAVE_TARDE_INICIO = "turno_tarde_inicio"
CHAVE_TARDE_FIM = "turno_tarde_fim"
CHAVE_ATIPICO_MIN = "atipico_min_minutos"
CHAVE_ATIPICO_MAX = "atipico_max_minutos"
CHAVE_EMAIL_TECNICO = "email_alerta_tecnico"
CHAVE_UNIDADES = "unidades_incluidas"


@dataclass(frozen=True, slots=True)
class ConfiguracaoRelatorio:
    turnos: ConfiguracaoTurnos = field(default_factory=ConfiguracaoTurnos)
    atipico_min_minutos: int = 1
    atipico_max_minutos: int = 180
    email_alerta_tecnico: str = "tecnologia@multilife.com.br"
    unidades: frozenset[int] = frozenset()

    @property
    def regras(self) -> RegrasMetricas:
        return RegrasMetricas(
            turnos=self.turnos,
            atipico_min=timedelta(minutes=self.atipico_min_minutos),
            atipico_max=timedelta(minutes=self.atipico_max_minutos),
        )

    @property
    def filtro(self) -> FiltroRelatorio:
        return FiltroRelatorio(unidades=self.unidades)

    def mesclar(self, valores: Mapping[str, str]) -> ConfiguracaoRelatorio:
        """Aplica os valores salvos no banco sobre os padrões (ignora vazios)."""

        def hora(chave: str, padrao: time) -> time:
            texto = valores.get(chave, "").strip()
            return time.fromisoformat(texto) if texto else padrao

        def inteiro(chave: str, padrao: int) -> int:
            texto = valores.get(chave, "").strip()
            return int(texto) if texto else padrao

        unidades = self.unidades
        if CHAVE_UNIDADES in valores:
            unidades = frozenset(
                int(parte) for parte in valores[CHAVE_UNIDADES].split(",") if parte.strip()
            )

        return replace(
            self,
            turnos=ConfiguracaoTurnos(
                manha_inicio=hora(CHAVE_MANHA_INICIO, self.turnos.manha_inicio),
                tarde_inicio=hora(CHAVE_TARDE_INICIO, self.turnos.tarde_inicio),
                tarde_fim=hora(CHAVE_TARDE_FIM, self.turnos.tarde_fim),
            ),
            atipico_min_minutos=inteiro(CHAVE_ATIPICO_MIN, self.atipico_min_minutos),
            atipico_max_minutos=inteiro(CHAVE_ATIPICO_MAX, self.atipico_max_minutos),
            email_alerta_tecnico=valores.get(CHAVE_EMAIL_TECNICO, "").strip()
            or self.email_alerta_tecnico,
            unidades=unidades,
        )

    def para_valores(self) -> dict[str, str]:
        return {
            CHAVE_MANHA_INICIO: f"{self.turnos.manha_inicio:%H:%M}",
            CHAVE_TARDE_INICIO: f"{self.turnos.tarde_inicio:%H:%M}",
            CHAVE_TARDE_FIM: f"{self.turnos.tarde_fim:%H:%M}",
            CHAVE_ATIPICO_MIN: str(self.atipico_min_minutos),
            CHAVE_ATIPICO_MAX: str(self.atipico_max_minutos),
            CHAVE_EMAIL_TECNICO: self.email_alerta_tecnico,
            CHAVE_UNIDADES: ",".join(str(u) for u in sorted(self.unidades)),
        }


@dataclass(frozen=True, slots=True)
class JanelaColeta:
    """Janela do polling (mesma do painel em tempo real): 06:00–18:00."""

    inicio: time = time(6, 0)
    fim: time = time(18, 0)
    intervalo: timedelta = timedelta(minutes=1)  # entre dois ciclos de coleta

    def do_dia(self, dia: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(dia, self.inicio, tzinfo=FUSO_BRASILIA),
            datetime.combine(dia, self.fim, tzinfo=FUSO_BRASILIA),
        )

    def contem(self, instante: datetime) -> bool:
        local = instante.astimezone(FUSO_BRASILIA).time()
        return self.inicio <= local <= self.fim


def inicio_do_dia(dia: date) -> datetime:
    return datetime.combine(dia, time.min, tzinfo=FUSO_BRASILIA)


def fim_do_dia(dia: date) -> datetime:
    return datetime.combine(dia, time.max, tzinfo=FUSO_BRASILIA)
