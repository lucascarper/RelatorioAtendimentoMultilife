"""Mapeamento DTO da API do SGG → entidades do domínio.

LGPD (RNF06): do agendamento só são lidos ID, agenda, unidade, data/hora, situação e
data de edição. Campos como ``id_funcionario``, ``data_nascimento``, ``sexo``, ``setor``,
``cargo`` e ``observacoes`` são descartados aqui e nunca chegam ao banco nem aos logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any

from relatorio.domain.entidades import FUSO_BRASILIA, Agenda, AgendamentoSgg, Situacao

FORMATO_FILTRO = "%Y-%m-%d %H:%M:%S"
_DATAS_VAZIAS = {"", "0000-00-00", "0000-00-00 00:00:00"}


def formatar_filtro(instante: datetime) -> str:
    """Filtros de data vão em horário de Brasília, formato ``AAAA-MM-DD HH:mm:ss``."""
    return instante.astimezone(FUSO_BRASILIA).strftime(FORMATO_FILTRO)


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _inteiro(valor: Any) -> int | None:
    texto = _texto(valor)
    if texto is None:
        return None
    try:
        return int(texto)
    except ValueError:
        return None


def _data(valor: Any) -> date | None:
    texto = _texto(valor)
    if texto is None or texto in _DATAS_VAZIAS:
        return None
    return date.fromisoformat(texto[:10])


def _hora(valor: Any) -> time | None:
    texto = _texto(valor)
    if texto is None:
        return None
    return time.fromisoformat(texto if len(texto) > 5 else f"{texto}:00")


def _data_hora(valor: Any) -> datetime | None:
    """A API devolve horário de Brasília sem fuso; o domínio só trabalha com fuso."""
    texto = _texto(valor)
    if texto is None or texto in _DATAS_VAZIAS:
        return None
    instante = datetime.fromisoformat(texto)
    if instante.tzinfo is None:
        return instante.replace(tzinfo=FUSO_BRASILIA)
    return instante


def para_agendamento(item: Mapping[str, Any]) -> AgendamentoSgg:
    """Converte um item de ``GET /agendamento/``. Lança ``ValueError`` se inválido."""
    id_agendamento = _inteiro(item.get("id_agendamento"))
    data_agendamento = _data(item.get("data_agendamento"))
    situacao = _texto(item.get("situacao"))
    if id_agendamento is None or data_agendamento is None or situacao is None:
        raise ValueError("agendamento sem id, data ou situação")
    return AgendamentoSgg(
        id_agendamento=id_agendamento,
        agenda_nome=_texto(item.get("agenda")) or "Agenda sem nome",
        id_unidade_atendimento=_inteiro(item.get("id_unidade_atendimento")),
        unidade_atendimento=_texto(item.get("unidade_atendimento")),
        data_agendamento=data_agendamento,
        hora_agendamento=_hora(item.get("hora_agendamento")),
        situacao=Situacao.de_texto(situacao),
        data_hora_edicao=_data_hora(item.get("data_hora_edicao")),
    )


def para_agenda(item: Mapping[str, Any]) -> Agenda:
    id_agenda = _inteiro(item.get("id_agenda"))
    if id_agenda is None:
        raise ValueError("agenda sem id")
    situacao = (_texto(item.get("situacao")) or "Ativa").casefold()
    return Agenda(
        id_agenda=id_agenda,
        nome=_texto(item.get("nome")) or f"Agenda {id_agenda}",
        sala=_texto(item.get("sala")),
        id_unidade_atendimento=_inteiro(item.get("id_unidade_atendimento")),
        unidade_atendimento=_texto(item.get("unidade_atendimento")),
        ativa=not situacao.startswith("inativ"),
    )
