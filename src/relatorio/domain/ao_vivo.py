"""Situação ao vivo do atendimento (monitor do gerente).

Complementa ``calcular_metricas`` com o que só faz sentido *durante* o dia: quem está
na recepção agora e há quanto tempo, quem está em atendimento, quem ainda não chegou
e o movimento hora a hora. Tudo é reconstruído pelo log de eventos até ``instante``,
com as mesmas regras do fechamento diário (a chegada é a entrada em "Aguardando"; o
fim do atendimento é a passagem para "Atendido").
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from relatorio.domain.entidades import FUSO_BRASILIA, Evento, Situacao
from relatorio.domain.metricas import AgendamentoDoDia, ordenar_eventos, situacao_em

TOLERANCIA_ATRASO = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class MovimentoHora:
    hora: int
    chegadas: int
    atendidos: int


@dataclass(frozen=True, slots=True)
class FilaArea:
    """Fila de uma área: recepção (agendas de guichê) ou consultórios (as demais)."""

    aguardando: int
    espera_media_s: int | None
    espera_maxima_s: int | None
    em_atendimento: int
    atendimento_maximo_s: int | None


@dataclass(frozen=True, slots=True)
class SituacaoAoVivo:
    instante: datetime
    aguardando: int
    espera_atual_media_s: int | None
    espera_atual_maxima_s: int | None
    em_atendimento: int
    atendimento_atual_maximo_s: int | None
    a_chegar: int
    a_chegar_atrasados: int
    por_hora: tuple[MovimentoHora, ...]
    # Com agendas marcadas como guichê: onde está a espera (recepção × consultório).
    tem_guiche: bool = False
    recepcao: FilaArea | None = None
    consultorio: FilaArea | None = None


def _ultimo(eventos: Sequence[Evento], situacao: Situacao) -> Evento | None:
    return next((e for e in reversed(eventos) if e.status_novo is situacao), None)


def _media(valores: Sequence[float]) -> int | None:
    return round(sum(valores) / len(valores)) if valores else None


def _maximo(valores: Sequence[float]) -> int | None:
    return round(max(valores)) if valores else None


class _Acumulador:
    def __init__(self) -> None:
        self.esperas: list[float] = []
        self.atendimentos: list[float] = []
        self.aguardando = 0
        self.em_atendimento = 0

    def fila(self) -> FilaArea:
        return FilaArea(
            aguardando=self.aguardando,
            espera_media_s=_media(self.esperas),
            espera_maxima_s=_maximo(self.esperas),
            em_atendimento=self.em_atendimento,
            atendimento_maximo_s=_maximo(self.atendimentos),
        )


def _segundos(inicio: datetime, fim: datetime) -> float | None:
    valor = (fim - inicio).total_seconds()
    return valor if valor >= 0 else None


def _registrar_fila(
    situacao: Situacao,
    do_dia: Sequence[Evento],
    instante: datetime,
    *acumuladores: _Acumulador,
) -> None:
    """Conta quem aguarda ou está em atendimento, com o tempo desde a última mudança."""
    desde = _ultimo(do_dia, situacao)
    tempo = _segundos(desde.ocorrido_em, instante) if desde else None
    for acumulador in acumuladores:
        if situacao is Situacao.AGUARDANDO:
            acumulador.aguardando += 1
            destino = acumulador.esperas
        else:
            acumulador.em_atendimento += 1
            destino = acumulador.atendimentos
        if tempo is not None:
            destino.append(tempo)


def calcular_ao_vivo(
    agendamentos: Iterable[AgendamentoDoDia],
    instante: datetime,
    *,
    abertura: time = time(6, 0),
    fechamento: time = time(18, 0),
    fuso: ZoneInfo = FUSO_BRASILIA,
    tolerancia_atraso: timedelta = TOLERANCIA_ATRASO,
    guiches: Collection[int] = frozenset(),
) -> SituacaoAoVivo:
    """Fila e movimento do dia até ``instante``.

    ``guiches`` são os IDs das agendas de recepção: quem aguarda num guichê está na
    espera da recepção; quem aguarda nas demais agendas, na espera do consultório.

    * Espera atual: desde a última entrada em "Aguardando" de quem ainda está lá.
    * Atendimento atual: desde a última chamada de quem está em "Em Atendimento".
    * A chegar: ainda "Agendado"; atrasado quando a hora agendada passou da tolerância.
    * Por hora: chegadas (primeira entrada em "Aguardando") e atendimentos finalizados,
      em faixas de uma hora entre ``abertura`` e ``fechamento``. O que acontece fora da
      janela entra na primeira ou na última faixa, para os totais baterem.
    """
    primeira_hora = abertura.hour
    ultima_hora = max(
        primeira_hora, (fechamento.hour - 1) if fechamento.minute == 0 else fechamento.hour
    )
    chegadas = dict.fromkeys(range(primeira_hora, ultima_hora + 1), 0)
    atendidos = dict.fromkeys(range(primeira_hora, ultima_hora + 1), 0)

    def faixa(momento: datetime) -> int:
        return min(max(momento.astimezone(fuso).hour, primeira_hora), ultima_hora)

    total, recepcao, consultorio = _Acumulador(), _Acumulador(), _Acumulador()
    a_chegar = atrasados = 0

    for agendamento in agendamentos:
        situacao = situacao_em(agendamento, instante)
        if situacao is Situacao.CANCELADO:
            continue
        do_dia = [
            e
            for e in ordenar_eventos(agendamento.eventos)
            if e.ocorrido_em <= instante
            and e.ocorrido_em.astimezone(fuso).date() == agendamento.data_agendamento
        ]

        chegada = next((e for e in do_dia if e.status_novo is Situacao.AGUARDANDO), None)
        if chegada is not None:
            chegadas[faixa(chegada.ocorrido_em)] += 1
        if situacao is Situacao.ATENDIDO:
            fim = _ultimo(do_dia, Situacao.ATENDIDO)
            if fim is not None:
                atendidos[faixa(fim.ocorrido_em)] += 1

        if situacao in (Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO):
            area = recepcao if agendamento.id_agenda in guiches else consultorio
            _registrar_fila(situacao, do_dia, instante, total, area)
        elif situacao is Situacao.AGENDADO:
            a_chegar += 1
            if agendamento.hora_agendamento is not None:
                horario = datetime.combine(
                    agendamento.data_agendamento, agendamento.hora_agendamento, tzinfo=fuso
                )
                if instante - horario > tolerancia_atraso:
                    atrasados += 1

    geral = total.fila()
    tem_guiche = bool(guiches)
    return SituacaoAoVivo(
        instante=instante,
        aguardando=geral.aguardando,
        espera_atual_media_s=geral.espera_media_s,
        espera_atual_maxima_s=geral.espera_maxima_s,
        em_atendimento=geral.em_atendimento,
        atendimento_atual_maximo_s=geral.atendimento_maximo_s,
        a_chegar=a_chegar,
        a_chegar_atrasados=atrasados,
        por_hora=tuple(
            MovimentoHora(hora=h, chegadas=chegadas[h], atendidos=atendidos[h]) for h in chegadas
        ),
        tem_guiche=tem_guiche,
        recepcao=recepcao.fila() if tem_guiche else None,
        consultorio=consultorio.fila() if tem_guiche else None,
    )
