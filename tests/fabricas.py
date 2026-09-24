"""Fábricas de objetos para os testes (dados sintéticos, sem dado pessoal)."""

from __future__ import annotations

from datetime import date, datetime, time

from relatorio.domain.entidades import (
    FUSO_BRASILIA,
    Agenda,
    AgendamentoSgg,
    Evento,
    OrigemEvento,
    Situacao,
    Snapshot,
)
from relatorio.domain.metricas import AgendamentoDoDia

DIA = date(2026, 9, 23)  # quarta-feira


def hora(texto: str, dia: date = DIA) -> datetime:
    """``"08:15"`` ou ``"08:15:30"`` → datetime com fuso de Brasília no dia de teste."""
    partes = [int(p) for p in texto.split(":")]
    while len(partes) < 3:
        partes.append(0)
    return datetime(dia.year, dia.month, dia.day, *partes, tzinfo=FUSO_BRASILIA)


def agenda(
    id_agenda: int,
    nome: str,
    sala: str | None = None,
    unidade: int | None = 1,
    incluir: bool = True,
) -> Agenda:
    return Agenda(
        id_agenda=id_agenda,
        nome=nome,
        sala=sala,
        id_unidade_atendimento=unidade,
        unidade_atendimento=f"Unidade {unidade}" if unidade is not None else None,
        incluir_relatorio=incluir,
    )


def evento(
    id_agendamento: int,
    anterior: Situacao | None,
    novo: Situacao,
    quando: str,
    dia: date = DIA,
) -> Evento:
    instante = hora(quando, dia)
    return Evento(
        id_agendamento=id_agendamento,
        status_anterior=anterior,
        status_novo=novo,
        ocorrido_em=instante,
        observado_em=instante,
        origem=OrigemEvento.POLLING,
    )


def atendimento(
    id_agendamento: int,
    *,
    id_agenda: int | None = 10,
    agenda_nome: str = "Clínico",
    hora_agendada: str | None = "08:00",
    chegada: str | None = "07:50",
    chamada: str | None = "08:05",
    fim: str | None = "08:20",
    unidade: int | None = 1,
    dia: date = DIA,
) -> AgendamentoDoDia:
    """Agendamento que percorre o fluxo completo (ou parte dele, se algum horário for None)."""
    eventos = [evento(id_agendamento, None, Situacao.AGENDADO, "06:00", dia)]
    situacao = Situacao.AGENDADO
    if chegada:
        eventos.append(evento(id_agendamento, situacao, Situacao.AGUARDANDO, chegada, dia))
        situacao = Situacao.AGUARDANDO
    if chamada:
        eventos.append(evento(id_agendamento, situacao, Situacao.EM_ATENDIMENTO, chamada, dia))
        situacao = Situacao.EM_ATENDIMENTO
    if fim:
        eventos.append(evento(id_agendamento, situacao, Situacao.ATENDIDO, fim, dia))
        situacao = Situacao.ATENDIDO
    return AgendamentoDoDia(
        id_agendamento=id_agendamento,
        id_agenda=id_agenda,
        agenda_nome=agenda_nome,
        id_unidade_atendimento=unidade,
        data_agendamento=dia,
        hora_agendamento=time.fromisoformat(hora_agendada) if hora_agendada else None,
        situacao_atual=situacao,
        eventos=tuple(eventos),
    )


def com_situacao(
    id_agendamento: int,
    situacao: Situacao,
    *,
    id_agenda: int | None = 10,
    agenda_nome: str = "Clínico",
    hora_agendada: str | None = "08:00",
    quando: str = "09:00",
    unidade: int | None = 1,
) -> AgendamentoDoDia:
    """Agendamento que saiu de Agendado direto para ``situacao`` (falta, cancelamento…)."""
    eventos = [evento(id_agendamento, None, Situacao.AGENDADO, "06:00")]
    if situacao is not Situacao.AGENDADO:
        eventos.append(evento(id_agendamento, Situacao.AGENDADO, situacao, quando))
    return AgendamentoDoDia(
        id_agendamento=id_agendamento,
        id_agenda=id_agenda,
        agenda_nome=agenda_nome,
        id_unidade_atendimento=unidade,
        data_agendamento=DIA,
        hora_agendamento=time.fromisoformat(hora_agendada) if hora_agendada else None,
        situacao_atual=situacao,
        eventos=tuple(eventos),
    )


def registro_sgg(
    id_agendamento: int,
    situacao: Situacao,
    edicao: str | None,
    *,
    agenda_nome: str = "Clínico",
    hora_agendada: str | None = "08:00",
    unidade: int | None = 1,
) -> AgendamentoSgg:
    return AgendamentoSgg(
        id_agendamento=id_agendamento,
        agenda_nome=agenda_nome,
        id_unidade_atendimento=unidade,
        unidade_atendimento=f"Unidade {unidade}" if unidade is not None else None,
        data_agendamento=DIA,
        hora_agendamento=time.fromisoformat(hora_agendada) if hora_agendada else None,
        situacao=situacao,
        data_hora_edicao=hora(edicao) if edicao else None,
    )


def snapshot(id_agendamento: int, situacao: Situacao, **extra: object) -> Snapshot:
    base: dict[str, object] = {
        "id_agendamento": id_agendamento,
        "id_agenda": 10,
        "agenda_nome": "Clínico",
        "id_unidade_atendimento": 1,
        "data_agendamento": DIA,
        "hora_agendamento": time(8, 0),
        "situacao_atual": situacao,
        "data_hora_edicao": hora("07:00"),
        "atualizado_em": hora("07:01"),
    }
    base.update(extra)
    return Snapshot(**base)  # type: ignore[arg-type]
