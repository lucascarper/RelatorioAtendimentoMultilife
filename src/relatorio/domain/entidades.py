"""Entidades do domínio.

O domínio não conhece a API do SGG, o banco nem a web: recebe e devolve apenas estes
objetos imutáveis. Nenhuma entidade carrega dado pessoal de paciente (LGPD) — somente
IDs do SGG, horários e situação.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


class Situacao(StrEnum):
    """Situações válidas de um agendamento na API do SGG."""

    AGENDADO = "Agendado"
    AGUARDANDO = "Aguardando"
    EM_ATENDIMENTO = "Em Atendimento"
    ATENDIDO = "Atendido"
    FALTOU = "Faltou"
    CANCELADO = "Cancelado"

    @classmethod
    def de_texto(cls, valor: str) -> Situacao:
        """Converte o texto da API tolerando caixa, acentos e espaços extras."""
        alvo = _normalizar(valor)
        for situacao in cls:
            if _normalizar(situacao.value) == alvo:
                return situacao
        raise ValueError(f"Situação desconhecida: {valor!r}")

    @property
    def final(self) -> bool:
        """Situações que o SGG não permite mais alterar."""
        return self in (Situacao.ATENDIDO, Situacao.FALTOU, Situacao.CANCELADO)


class OrigemEvento(StrEnum):
    POLLING = "polling"
    RECONCILIACAO = "reconciliacao"
    VERIFICACAO_FALTA = "verificacao_falta"


class Turno(StrEnum):
    MANHA = "manha"
    TARDE = "tarde"

    @property
    def rotulo(self) -> str:
        return "Manhã" if self is Turno.MANHA else "Tarde"


@dataclass(frozen=True, slots=True)
class AgendamentoSgg:
    """Registro de agendamento como chega da API, já sem campos pessoais."""

    id_agendamento: int
    agenda_nome: str
    id_unidade_atendimento: int | None
    unidade_atendimento: str | None
    data_agendamento: date
    hora_agendamento: time | None
    situacao: Situacao
    data_hora_edicao: datetime | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Último estado conhecido de um agendamento (base para detectar mudanças)."""

    id_agendamento: int
    id_agenda: int | None
    agenda_nome: str
    id_unidade_atendimento: int | None
    data_agendamento: date
    hora_agendamento: time | None
    situacao_atual: Situacao
    data_hora_edicao: datetime | None
    atualizado_em: datetime


@dataclass(frozen=True, slots=True)
class Evento:
    """Transição de status observada. Imutável: o log só recebe inserções."""

    id_agendamento: int
    status_anterior: Situacao | None
    status_novo: Situacao
    ocorrido_em: datetime
    observado_em: datetime
    origem: OrigemEvento


@dataclass(frozen=True, slots=True)
class Agenda:
    """Agenda do SGG. Cada agenda corresponde a um consultório."""

    id_agenda: int
    nome: str
    sala: str | None
    id_unidade_atendimento: int | None
    unidade_atendimento: str | None
    ativa: bool = True
    incluir_relatorio: bool = True

    @property
    def consultorio(self) -> str:
        """Nome exibido: campo `sala`, com fallback para o nome da agenda."""
        sala = (self.sala or "").strip()
        return sala or self.nome
