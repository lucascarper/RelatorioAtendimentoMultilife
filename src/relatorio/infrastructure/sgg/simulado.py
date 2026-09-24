"""SGG simulado: gera um dia sintético e responde como a API real.

Usado na prévia do e-mail, no modo demonstração e no teste ponta a ponta. Cada
agendamento tem uma linha do tempo verdadeira (instante → situação); a API simulada
devolve só o **status atual** e a ``data_hora_edicao`` da última mudança — exatamente
a limitação que obriga o polling durante o expediente (seção 3).

Todos os dados são fictícios (sem pessoas reais).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from relatorio.domain.entidades import (
    FUSO_BRASILIA,
    Agenda,
    AgendamentoSgg,
    Situacao,
)
from relatorio.infrastructure.sgg.erros import ErroSggServidor

UNIDADE_ID = 1
UNIDADE_NOME = "Unidade Centro"


@dataclass(frozen=True, slots=True)
class PerfilAgenda:
    agenda: Agenda
    atendimento_min: float
    desvio_min: float
    peso: float


PERFIS: tuple[PerfilAgenda, ...] = tuple(
    PerfilAgenda(
        Agenda(id_agenda, nome, sala, UNIDADE_ID, UNIDADE_NOME),
        media,
        desvio,
        peso,
    )
    for id_agenda, nome, sala, media, desvio, peso in (
        (101, "Clínico Geral", "Consultório 01", 13, 4, 0.30),
        (102, "Clínico Geral 2", "Consultório 02", 15, 5, 0.22),
        (103, "Audiometria", "Cabine Audiométrica", 9, 2, 0.18),
        (104, "Acuidade Visual", "Sala 03", 5, 1.5, 0.12),
        (105, "Eletrocardiograma", "Sala 04", 11, 3, 0.10),
        (106, "Raio X", "Sala de Imagem", 7, 2, 0.08),
    )
)


@dataclass(frozen=True, slots=True)
class AgendamentoSimulado:
    id_agendamento: int
    agenda: Agenda
    data: date
    hora: time | None
    linha_do_tempo: tuple[tuple[datetime, Situacao], ...]
    caso: str = "normal"

    def estado_em(self, instante: datetime) -> tuple[Situacao, datetime] | None:
        estado: tuple[Situacao, datetime] | None = None
        for quando, situacao in self.linha_do_tempo:
            if quando <= instante:
                estado = (situacao, quando)
        return estado

    def registro_em(self, instante: datetime) -> AgendamentoSgg | None:
        estado = self.estado_em(instante)
        if estado is None:
            return None
        situacao, edicao = estado
        return AgendamentoSgg(
            id_agendamento=self.id_agendamento,
            agenda_nome=self.agenda.nome,
            id_unidade_atendimento=self.agenda.id_unidade_atendimento,
            unidade_atendimento=self.agenda.unidade_atendimento,
            data_agendamento=self.data,
            hora_agendamento=self.hora,
            situacao=situacao,
            data_hora_edicao=edicao,
        )

    def instante(self, situacao: Situacao) -> datetime | None:
        return next((q for q, s in self.linha_do_tempo if s is situacao), None)

    @property
    def situacao_final(self) -> Situacao:
        return self.linha_do_tempo[-1][1]


def _em(dia: date, minutos: float) -> datetime:
    base = datetime.combine(dia, time.min, tzinfo=FUSO_BRASILIA)
    return base + timedelta(seconds=round(minutos * 60))


def _transicoes(
    caso: str,
    dia: date,
    minuto: float,
    perfil: PerfilAgenda,
    rnd: random.Random,
    livre_em: dict[int, float],
) -> list[tuple[datetime, Situacao]]:
    """Transições do dia de um agendamento, conforme o caso sorteado."""
    if caso == "sem_baixa":
        return []
    if caso == "cancelado":
        return [(_em(dia, rnd.uniform(6 * 60, minuto - 30)), Situacao.CANCELADO)]
    if caso == "falta":
        return [(_em(dia, minuto + rnd.uniform(40, 90)), Situacao.FALTOU)]
    if caso == "falta_na_agenda":
        # Recepção registra a falta depois do expediente: só a conferência noturna vê.
        return [(_em(dia, 19 * 60 + rnd.uniform(0, 30)), Situacao.FALTOU)]

    chegada = minuto + rnd.uniform(-20, 8)
    id_agenda = perfil.agenda.id_agenda
    inicio = max(chegada + rnd.uniform(2, 6), livre_em.get(id_agenda, 0.0))
    duracao = max(2.0, rnd.gauss(perfil.atendimento_min, perfil.desvio_min))
    if caso == "atipico_curto":
        inicio = float(int(inicio)) + 40 / 60  # cruza a virada do minuto
        duracao = 40 / 60
    elif caso == "atipico_longo":
        duracao = 190.0  # atendimento esquecido aberto no sistema
    fim = inicio + duracao
    if caso != "atipico_longo":
        livre_em[id_agenda] = fim

    transicoes = [(_em(dia, chegada), Situacao.AGUARDANDO)]
    if caso == "salto":
        return [*transicoes, (_em(dia, fim), Situacao.ATENDIDO)]
    transicoes.append((_em(dia, inicio), Situacao.EM_ATENDIMENTO))
    if caso != "em_atendimento_aberto":
        transicoes.append((_em(dia, fim), Situacao.ATENDIDO))
    return transicoes


def gerar_dia(dia: date, semente: int = 7, volume: int = 120) -> list[AgendamentoSimulado]:
    """Gera um dia com filas por consultório e os casos especiais da documentação."""
    rnd = random.Random(f"{dia.isoformat()}-{semente}")  # noqa: S311 — dado fictício
    pesos = [p.peso for p in PERFIS]
    brutos: list[tuple[int, PerfilAgenda, float, str]] = []
    for indice in range(volume):
        perfil = rnd.choices(PERFIS, weights=pesos)[0]
        manha = rnd.random() < 0.6
        slot = rnd.randrange(7 * 6, 12 * 6 + 6) if manha else rnd.randrange(13 * 6, 17 * 6 + 4)
        # IDs únicos entre dias, como no SGG real.
        brutos.append((dia.toordinal() % 100_000 * 1_000 + indice, perfil, slot * 10.0, "normal"))

    # Casos especiais (seções 4 e 15): um de cada, em agendamentos sorteados.
    especiais = (
        ["cancelado"] * 4
        + ["falta"] * 10
        + [
            "sem_baixa",
            "sem_baixa",
            "falta_na_agenda",
            "atipico_curto",
            "atipico_longo",
            "salto",
            "em_atendimento_aberto",
        ]
    )
    if volume < len(especiais) * 2:
        especiais = especiais[: volume // 4]  # dias fracos: só alguns casos especiais
    posicoes = rnd.sample(range(volume), len(especiais))
    for posicao, caso in zip(posicoes, especiais, strict=True):
        id_agendamento, perfil, minuto, _ = brutos[posicao]
        if caso == "atipico_longo":
            minuto = 8 * 60 + 30.0  # termina dentro do expediente, visível ao polling
        brutos[posicao] = (id_agendamento, perfil, minuto, caso)

    resultado: list[AgendamentoSimulado] = []
    livre_em: dict[int, float] = {}  # fila de cada consultório (minuto em que fica livre)
    for id_agendamento, perfil, minuto, caso in sorted(brutos, key=lambda b: b[2]):
        criacao = _em(dia, -rnd.uniform(24 * 60, 5 * 24 * 60))
        linha = [
            (criacao, Situacao.AGENDADO),
            *_transicoes(caso, dia, minuto, perfil, rnd, livre_em),
        ]
        resultado.append(
            AgendamentoSimulado(
                id_agendamento=id_agendamento,
                agenda=perfil.agenda,
                data=dia,
                hora=time(int(minuto // 60), int(minuto % 60)),
                linha_do_tempo=tuple(linha),
                caso=caso,
            )
        )
    return resultado


@dataclass
class SggSimulado:
    """Implementa ``SggGateway`` sobre dias gerados por ``gerar_dia``."""

    agendamentos: Sequence[AgendamentoSimulado]
    agora: Callable[[], datetime]
    indisponivel: Sequence[tuple[datetime, datetime]] = ()
    lista_agendas: Sequence[Agenda] = field(default_factory=lambda: [p.agenda for p in PERFIS])
    requisicoes_realizadas: int = 0

    def _requisicao(self) -> datetime:
        self.requisicoes_realizadas += 1
        agora = self.agora()
        if any(inicio <= agora < fim for inicio, fim in self.indisponivel):
            raise ErroSggServidor("Erro interno do Servidor (simulado)", "S000")
        return agora

    def agendamentos_editados(self, de: datetime, ate: datetime) -> list[AgendamentoSgg]:
        self._requisicao()
        registros = []
        for ag in self.agendamentos:
            if any(de < quando <= ate for quando, _ in ag.linha_do_tempo):
                registro = ag.registro_em(ate)
                if registro is not None:
                    registros.append(registro)
        return registros

    def agendamentos_do_dia(
        self, dia: date, situacao: Situacao | None = None
    ) -> list[AgendamentoSgg]:
        agora = self._requisicao()
        registros = []
        for ag in self.agendamentos:
            registro = ag.registro_em(agora) if ag.data == dia else None
            if registro is not None and (situacao is None or registro.situacao is situacao):
                registros.append(registro)
        return registros

    def agendas(self) -> list[Agenda]:
        self._requisicao()
        return list(self.lista_agendas)
