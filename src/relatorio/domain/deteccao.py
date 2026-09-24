"""Detecção de transição de status (seção 10 da documentação técnica).

A cada registro recebido do SGG:

1. compara com o último snapshot gravado;
2. se não existe snapshot ou a situação mudou, gera um evento (anterior → novo) com
   ``ocorrido_em = data_hora_edicao`` — desde que essa edição seja mais nova que a
   última observada e não esteja no futuro; caso contrário usa o horário da observação;
3. se o status pulou etapas (ex.: Aguardando → Atendido), o evento é gravado e marcado
   como salto: conta nos totais, mas o atendimento fica "sem tempo medido".

A mesma entrada processada duas vezes não gera um segundo evento: o snapshot já estará
na situação nova. O UNIQUE (id_agendamento, status_novo, ocorrido_em) do banco é a
segunda barreira contra duplicidade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from relatorio.domain.entidades import (
    AgendamentoSgg,
    Evento,
    OrigemEvento,
    Situacao,
    Snapshot,
)

# Caminho principal do atendimento. Avançar mais de uma posição de uma vez é um salto.
_ORDEM_FLUXO: dict[Situacao, int] = {
    Situacao.AGENDADO: 0,
    Situacao.AGUARDANDO: 1,
    Situacao.EM_ATENDIMENTO: 2,
    Situacao.ATENDIDO: 3,
}


def eh_salto(anterior: Situacao | None, novo: Situacao) -> bool:
    """Verdadeiro quando a transição pula etapas do fluxo principal."""
    origem = _ORDEM_FLUXO.get(anterior or Situacao.AGENDADO)
    destino = _ORDEM_FLUXO.get(novo)
    if origem is None or destino is None:
        return False
    return destino - origem > 1


def resolver_ocorrido_em(
    data_hora_edicao: datetime | None,
    edicao_anterior: datetime | None,
    observado_em: datetime,
) -> datetime:
    """Horário mais fiel da transição.

    ``data_hora_edicao`` marca exatamente a edição que mudou o status, então é preferida
    quando é posterior à última edição conhecida e não está no futuro em relação à
    observação. Nos demais casos (relógio inconsistente, campo vazio), vale o horário
    em que o coletor viu a mudança.
    """
    if data_hora_edicao is None:
        return observado_em
    if edicao_anterior is not None and data_hora_edicao <= edicao_anterior:
        return observado_em
    if data_hora_edicao > observado_em:
        return observado_em
    return data_hora_edicao


@dataclass(frozen=True, slots=True)
class Deteccao:
    """Resultado do processamento de um registro.

    ``snapshot`` é None quando nada mudou; ``evento`` é None quando só campos auxiliares
    (hora, agenda, edição) mudaram, sem troca de situação.
    """

    evento: Evento | None
    snapshot: Snapshot | None
    salto: bool = False


def detectar_transicao(
    registro: AgendamentoSgg,
    atual: Snapshot | None,
    *,
    id_agenda: int | None,
    observado_em: datetime,
    origem: OrigemEvento,
) -> Deteccao:
    anterior = atual.situacao_atual if atual else None

    novo_snapshot = Snapshot(
        id_agendamento=registro.id_agendamento,
        id_agenda=id_agenda if id_agenda is not None else (atual.id_agenda if atual else None),
        agenda_nome=registro.agenda_nome,
        id_unidade_atendimento=registro.id_unidade_atendimento,
        data_agendamento=registro.data_agendamento,
        hora_agendamento=registro.hora_agendamento,
        situacao_atual=registro.situacao,
        data_hora_edicao=registro.data_hora_edicao,
        atualizado_em=observado_em,
    )

    if atual is not None and atual.situacao_atual is registro.situacao:
        campos_iguais = (
            atual.id_agenda == novo_snapshot.id_agenda
            and atual.agenda_nome == novo_snapshot.agenda_nome
            and atual.id_unidade_atendimento == novo_snapshot.id_unidade_atendimento
            and atual.data_agendamento == novo_snapshot.data_agendamento
            and atual.hora_agendamento == novo_snapshot.hora_agendamento
            and atual.data_hora_edicao == novo_snapshot.data_hora_edicao
        )
        return Deteccao(evento=None, snapshot=None if campos_iguais else novo_snapshot)

    ocorrido_em = resolver_ocorrido_em(
        registro.data_hora_edicao,
        atual.data_hora_edicao if atual else None,
        observado_em,
    )
    evento = Evento(
        id_agendamento=registro.id_agendamento,
        status_anterior=anterior,
        status_novo=registro.situacao,
        ocorrido_em=ocorrido_em,
        observado_em=observado_em,
        origem=origem,
    )
    return Deteccao(
        evento=evento,
        snapshot=novo_snapshot,
        salto=eh_salto(anterior, registro.situacao),
    )
