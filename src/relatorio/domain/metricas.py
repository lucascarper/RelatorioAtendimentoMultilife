"""Cálculo das métricas de atendimento (seção 4 da documentação técnica).

Toda métrica sai das transições de status gravadas em ``agendamento_evento``:

* tempo de espera = "Em Atendimento" − "Aguardando" (nos consultórios, a última espera
  quando há guichês: a do guichê não entra);
* tempo de atendimento = "Atendido" − "Em Atendimento";
* TMA = média dos tempos de atendimento válidos (não atípicos, sem salto de status).

As funções aqui são puras: recebem os dados já carregados e devolvem estruturas
imutáveis. Isso permite testar todas as regras sem API nem banco e reaproveitar o mesmo
cálculo para o fechamento diário e para uma futura consulta em tempo real
(``calcular_metricas`` aceita qualquer período ``[inicio, fim]``).

Mudou alguma regra? Suba ``VERSAO_REGRA`` e reprocesse os dias afetados.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from relatorio.domain.entidades import FUSO_BRASILIA, Agenda, Evento, Situacao, Turno
from relatorio.domain.turnos import ConfiguracaoTurnos

VERSAO_REGRA = "1.2.0"  # 1.1: guichês e turnos; 1.2: espera do consultório = última espera


# --------------------------------------------------------------------------- entradas


@dataclass(frozen=True, slots=True)
class RegrasMetricas:
    turnos: ConfiguracaoTurnos = field(default_factory=ConfiguracaoTurnos)
    atipico_min: timedelta = timedelta(minutes=1)
    atipico_max: timedelta = timedelta(minutes=180)
    fuso: ZoneInfo = FUSO_BRASILIA

    def __post_init__(self) -> None:
        if not timedelta(0) <= self.atipico_min < self.atipico_max:
            raise ValueError("Limites de atípico inválidos: é preciso 0 ≤ mínimo < máximo")


@dataclass(frozen=True, slots=True)
class AgendamentoDoDia:
    """Um agendamento do período com todos os seus eventos conhecidos."""

    id_agendamento: int
    id_agenda: int | None
    agenda_nome: str
    id_unidade_atendimento: int | None
    data_agendamento: date
    hora_agendamento: time | None
    situacao_atual: Situacao
    eventos: tuple[Evento, ...] = ()


@dataclass(frozen=True, slots=True)
class FalhaColeta:
    inicio: datetime
    fim: datetime

    @property
    def minutos(self) -> int:
        return round((self.fim - self.inicio).total_seconds() / 60)


# --------------------------------------------------------------------------- saídas


@dataclass(frozen=True, slots=True)
class Kpis:
    agendados: int
    cancelados: int
    atendimentos: int
    faltas: int
    taxa_faltas: float | None
    sem_baixa: int
    espera_media_s: int | None
    espera_mediana_s: int | None
    espera_maxima_s: int | None
    tma_s: int | None
    atendimentos_medidos: int
    sem_turno: int
    # Com guichês marcados (senão None): consultórios × recepção (guichês).
    atendimentos_consultorios: int | None = None
    atendimentos_guiches: int | None = None
    espera_consultorio_s: int | None = None
    espera_recepcao_s: int | None = None
    tma_consultorios_s: int | None = None
    tma_guiches_s: int | None = None

    @property
    def tem_guiche(self) -> bool:
        return self.atendimentos_guiches is not None

    @classmethod
    def de_dict(cls, dados: Mapping[str, object]) -> Kpis:
        def inteiro(chave: str) -> int:
            valor = dados.get(chave, 0)
            return int(valor) if isinstance(valor, int | float) else 0

        def opcional(chave: str) -> int | None:
            valor = dados.get(chave)
            return int(valor) if isinstance(valor, int | float) else None

        taxa = dados.get("taxa_faltas")
        return cls(
            agendados=inteiro("agendados"),
            cancelados=inteiro("cancelados"),
            atendimentos=inteiro("atendimentos"),
            faltas=inteiro("faltas"),
            taxa_faltas=float(taxa) if isinstance(taxa, int | float) else None,
            sem_baixa=inteiro("sem_baixa"),
            espera_media_s=opcional("espera_media_s"),
            espera_mediana_s=opcional("espera_mediana_s"),
            espera_maxima_s=opcional("espera_maxima_s"),
            tma_s=opcional("tma_s"),
            atendimentos_medidos=inteiro("atendimentos_medidos"),
            sem_turno=inteiro("sem_turno"),
            atendimentos_consultorios=opcional("atendimentos_consultorios"),
            atendimentos_guiches=opcional("atendimentos_guiches"),
            espera_consultorio_s=opcional("espera_consultorio_s"),
            espera_recepcao_s=opcional("espera_recepcao_s"),
            tma_consultorios_s=opcional("tma_consultorios_s"),
            tma_guiches_s=opcional("tma_guiches_s"),
        )


@dataclass(frozen=True, slots=True)
class ResumoTurno:
    rotulo: str
    faixa: str
    agendados: int
    atendimentos: int
    faltas: int
    taxa_faltas: float | None
    espera_media_s: int | None
    espera_mediana_s: int | None
    espera_maxima_s: int | None
    tma_s: int | None
    atendimentos_medidos: int


@dataclass(frozen=True, slots=True)
class CelulaTma:
    atendimentos: int
    tma_s: int | None


@dataclass(frozen=True, slots=True)
class LinhaConsultorio:
    id_agenda: int | None
    consultorio: str
    agenda: str
    manha: CelulaTma
    tarde: CelulaTma
    total: CelulaTma


@dataclass(frozen=True, slots=True)
class LinhaConsultorioTurno:
    """TMA de um consultório num turno (na troca de turno costuma trocar o médico)."""

    id_agenda: int | None
    consultorio: str
    agenda: str
    guiche: bool
    atendimentos: int
    atendimentos_medidos: int
    tma_s: int | None


@dataclass(frozen=True, slots=True)
class LinhaAgenda:
    id_agenda: int | None
    agenda: str
    consultorio: str
    agendados: int
    atendimentos: int
    atendimentos_medidos: int
    media_s: int | None
    mediana_s: int | None
    maximo_s: int | None
    guiche: bool = False


@dataclass(frozen=True, slots=True)
class AtendimentoAtipico:
    id_agendamento: int
    consultorio: str
    hora_agendada: str | None
    duracao_s: int
    motivo: str


@dataclass(frozen=True, slots=True)
class ListaIds:
    total: int
    ids: tuple[int, ...]

    @classmethod
    def de(cls, ids: Iterable[int]) -> ListaIds:
        ordenados = tuple(sorted(set(ids)))
        return cls(total=len(ordenados), ids=ordenados)


@dataclass(frozen=True, slots=True)
class Alertas:
    atipicos: tuple[AtendimentoAtipico, ...]
    sem_baixa: ListaIds
    em_atendimento_aberto: ListaIds
    sem_tempo_medido: ListaIds
    faltas_confirmadas_na_agenda: ListaIds
    falhas_coleta: tuple[FalhaColeta, ...]
    verificacao_faltas_indisponivel: bool

    @property
    def quantidade(self) -> int:
        """Quantos tipos de alerta estão ativos (para o selo do cabeçalho)."""
        return sum(
            (
                bool(self.atipicos),
                self.sem_baixa.total > 0,
                self.em_atendimento_aberto.total > 0,
                self.sem_tempo_medido.total > 0,
                bool(self.falhas_coleta),
                self.verificacao_faltas_indisponivel,
            )
        )


@dataclass(frozen=True, slots=True)
class MetricasPeriodo:
    versao_regra: str
    inicio: datetime
    fim: datetime
    limites_atipico_minutos: tuple[int, int]
    kpis: Kpis
    por_turno: dict[str, ResumoTurno]
    consultorios: tuple[LinhaConsultorio, ...]
    agendas: tuple[LinhaAgenda, ...]
    alertas: Alertas
    # "agendas" (consultórios e demais) e "guiches" → turno → resumo.
    por_turno_grupos: dict[str, dict[str, ResumoTurno]] = field(default_factory=dict)
    # "agendas" (consultórios e demais) e "guiches" → resumo do período inteiro.
    por_grupo: dict[str, ResumoTurno] = field(default_factory=dict)
    consultorios_por_turno: dict[str, tuple[LinhaConsultorioTurno, ...]] = field(
        default_factory=dict
    )
    agendas_por_turno: dict[str, tuple[LinhaAgenda, ...]] = field(default_factory=dict)

    @property
    def sem_movimento(self) -> bool:
        return self.kpis.agendados == 0


# --------------------------------------------------------------------------- regras


def _local(instante: datetime, fuso: ZoneInfo) -> datetime:
    return instante.astimezone(fuso)


def ordenar_eventos(eventos: Iterable[Evento]) -> tuple[Evento, ...]:
    return tuple(sorted(eventos, key=lambda e: (e.ocorrido_em, e.observado_em)))


def situacao_em(agendamento: AgendamentoDoDia, instante: datetime) -> Situacao:
    """Situação do agendamento no instante informado, reconstruída pelo log de eventos."""
    eventos = ordenar_eventos(agendamento.eventos)
    anteriores = [e for e in eventos if e.ocorrido_em <= instante]
    if anteriores:
        return anteriores[-1].status_novo
    if eventos:
        return eventos[0].status_anterior or Situacao.AGENDADO
    return agendamento.situacao_atual


@dataclass(frozen=True, slots=True)
class Tempos:
    espera: timedelta | None
    atendimento: timedelta | None


def medir_tempos(
    eventos: Sequence[Evento], ate: datetime, *, ultima_espera: bool = False
) -> Tempos:
    """Mede espera e atendimento a partir das transições observadas até ``ate``.

    * Espera: da primeira chegada ("Aguardando") até a primeira chamada vinda da espera
      ("Aguardando" → "Em Atendimento").
    * Com ``ultima_espera`` (agendas de consultório quando há guichês): da **última**
      entrada em "Aguardando" até a chamada seguinte. Se o agendamento foi chamado
      antes (no guichê) e devolvido à espera, só conta a espera pelo consultório; se
      ainda está aguardando de novo, a espera ainda não terminou (None).
    * Atendimento: da última chamada até "Atendido", apenas quando o evento de
      finalização veio de "Em Atendimento". Se o status pulou etapas, o tempo não é
      medido (o atendimento conta nos totais, mas fica fora das médias).
    """
    validos = [e for e in ordenar_eventos(eventos) if e.ocorrido_em <= ate]

    entradas = [e for e in validos if e.status_novo is Situacao.AGUARDANDO]
    chegada = (entradas[-1] if ultima_espera else entradas[0]) if entradas else None
    espera: timedelta | None = None
    if chegada is not None:
        chamada = next(
            (
                e
                for e in validos
                if e.status_novo is Situacao.EM_ATENDIMENTO
                and e.status_anterior is Situacao.AGUARDANDO
                and e.ocorrido_em >= chegada.ocorrido_em
            ),
            None,
        )
        if chamada is not None:
            espera = chamada.ocorrido_em - chegada.ocorrido_em

    atendimento: timedelta | None = None
    finalizacao = next((e for e in reversed(validos) if e.status_novo is Situacao.ATENDIDO), None)
    if finalizacao is not None and finalizacao.status_anterior is Situacao.EM_ATENDIMENTO:
        inicio = next(
            (
                e
                for e in reversed(validos)
                if e.status_novo is Situacao.EM_ATENDIMENTO
                and e.ocorrido_em <= finalizacao.ocorrido_em
            ),
            None,
        )
        if inicio is not None:
            atendimento = finalizacao.ocorrido_em - inicio.ocorrido_em

    return Tempos(espera=espera, atendimento=atendimento)


def definir_turno(
    agendamento: AgendamentoDoDia, turnos: ConfiguracaoTurnos, fuso: ZoneInfo
) -> Turno | None:
    """Turno pela hora agendada; sem hora (agenda por ordem de chegada), usa a chegada."""
    if agendamento.hora_agendamento is not None:
        return turnos.classificar(agendamento.hora_agendamento)
    eventos = ordenar_eventos(agendamento.eventos)
    do_dia = [
        e for e in eventos if _local(e.ocorrido_em, fuso).date() == agendamento.data_agendamento
    ]
    chegada = next((e for e in do_dia if e.status_novo is Situacao.AGUARDANDO), None)
    referencia = chegada or (do_dia[0] if do_dia else None)
    if referencia is None:
        return None
    return turnos.classificar(_local(referencia.ocorrido_em, fuso).time())


def classificar_atipico(duracao: timedelta, regras: RegrasMetricas) -> str | None:
    if duracao < regras.atipico_min:
        return f"menos de {_minutos(regras.atipico_min)} min"
    if duracao > regras.atipico_max:
        return f"mais de {_minutos(regras.atipico_max)} min"
    return None


def _minutos(valor: timedelta) -> int:
    return round(valor.total_seconds() / 60)


def _media(valores: Sequence[float]) -> int | None:
    return round(statistics.fmean(valores)) if valores else None


def _mediana(valores: Sequence[float]) -> int | None:
    return round(statistics.median(valores)) if valores else None


def _maximo(valores: Sequence[float]) -> int | None:
    return round(max(valores)) if valores else None


def _taxa(parte: int, todo: int) -> float | None:
    return parte / todo if todo else None


# --------------------------------------------------------------------------- agregação


@dataclass(slots=True)
class _Linha:
    """Agendamento já classificado (uso interno do cálculo)."""

    id_agendamento: int
    chave: str
    id_agenda: int | None
    agenda: str
    consultorio: str
    hora_agendada: str | None
    turno: Turno | None
    situacao: Situacao
    guiche: bool = False
    espera_s: float | None = None
    atendimento_s: float | None = None
    atipico: str | None = None
    medido: bool = False


def _classificar(
    agendamento: AgendamentoDoDia,
    agendas: Mapping[int, Agenda],
    regras: RegrasMetricas,
    fim: datetime,
    faltas_confirmadas: frozenset[int],
    tem_guiche: bool = False,
) -> tuple[_Linha, bool]:
    """Classifica um agendamento. Devolve a linha e se a falta veio da conferência (RF11)."""
    agenda = agendas.get(agendamento.id_agenda) if agendamento.id_agenda is not None else None
    situacao = situacao_em(agendamento, fim)
    falta_confirmada = agendamento.id_agendamento in faltas_confirmadas and situacao in (
        Situacao.AGENDADO,
        Situacao.AGUARDANDO,
        Situacao.FALTOU,
    )
    if falta_confirmada:
        situacao = Situacao.FALTOU

    chave = (
        f"id:{agendamento.id_agenda}"
        if agendamento.id_agenda is not None
        else (f"nome:{agendamento.agenda_nome}")
    )
    linha = _Linha(
        id_agendamento=agendamento.id_agendamento,
        chave=chave,
        id_agenda=agendamento.id_agenda,
        agenda=agenda.nome if agenda else agendamento.agenda_nome,
        consultorio=agenda.consultorio if agenda else agendamento.agenda_nome,
        hora_agendada=(
            agendamento.hora_agendamento.strftime("%H:%M") if agendamento.hora_agendamento else None
        ),
        turno=definir_turno(agendamento, regras.turnos, regras.fuso),
        situacao=situacao,
        guiche=bool(agenda and agenda.guiche),
    )

    # Espera e atendimento só com eventos do próprio dia do agendamento: um evento
    # antigo (criação, remarcação) nunca vira "espera de vários dias".
    do_dia = [
        e
        for e in agendamento.eventos
        if _local(e.ocorrido_em, regras.fuso).date() == agendamento.data_agendamento
    ]
    tempos = medir_tempos(do_dia, fim, ultima_espera=not linha.guiche and tem_guiche)
    if tempos.espera is not None:
        linha.espera_s = tempos.espera.total_seconds()
    if situacao is Situacao.ATENDIDO and tempos.atendimento is not None:
        linha.atendimento_s = tempos.atendimento.total_seconds()
        linha.atipico = classificar_atipico(tempos.atendimento, regras)
        linha.medido = linha.atipico is None
    return linha, falta_confirmada


def _resumo_turno(linhas: Sequence[_Linha], rotulo: str, faixa: str) -> ResumoTurno:
    nao_canceladas = [x for x in linhas if x.situacao is not Situacao.CANCELADO]
    atendidas = [x for x in linhas if x.situacao is Situacao.ATENDIDO]
    faltas = sum(1 for x in linhas if x.situacao is Situacao.FALTOU)
    esperas = [x.espera_s for x in nao_canceladas if x.espera_s is not None]
    duracoes = [x.atendimento_s for x in atendidas if x.medido and x.atendimento_s is not None]
    return ResumoTurno(
        rotulo=rotulo,
        faixa=faixa,
        agendados=len(nao_canceladas),
        atendimentos=len(atendidas),
        faltas=faltas,
        taxa_faltas=_taxa(faltas, len(nao_canceladas)),
        espera_media_s=_media(esperas),
        espera_mediana_s=_mediana(esperas),
        espera_maxima_s=_maximo(esperas),
        tma_s=_media(duracoes),
        atendimentos_medidos=len(duracoes),
    )


def _celula(linhas: Sequence[_Linha]) -> CelulaTma:
    atendidas = [x for x in linhas if x.situacao is Situacao.ATENDIDO]
    duracoes = [x.atendimento_s for x in atendidas if x.medido and x.atendimento_s is not None]
    return CelulaTma(atendimentos=len(atendidas), tma_s=_media(duracoes))


def _linha_agenda(grupo: Sequence[_Linha]) -> LinhaAgenda:
    referencia = grupo[0]
    atendidas = [x for x in grupo if x.situacao is Situacao.ATENDIDO]
    duracoes = [x.atendimento_s for x in atendidas if x.medido and x.atendimento_s is not None]
    return LinhaAgenda(
        id_agenda=referencia.id_agenda,
        agenda=referencia.agenda,
        consultorio=referencia.consultorio,
        agendados=len(grupo),
        atendimentos=len(atendidas),
        atendimentos_medidos=len(duracoes),
        media_s=_media(duracoes),
        mediana_s=_mediana(duracoes),
        maximo_s=_maximo(duracoes),
        guiche=referencia.guiche,
    )


def _ordem_agenda(linha: LinhaAgenda) -> tuple[int, str]:
    return (-linha.atendimentos, linha.agenda.lower())


def _ordem_tma_turno(linha: LinhaConsultorioTurno) -> tuple[int, float, str]:
    return (1 if linha.tma_s is None else 0, -(linha.tma_s or 0), linha.consultorio.lower())


def _por_turno(
    grupos: Mapping[str, Sequence[_Linha]],
) -> tuple[dict[str, tuple[LinhaConsultorioTurno, ...]], dict[str, tuple[LinhaAgenda, ...]]]:
    """Consultórios (TMA) e agendas (tempos) separados por turno.

    Um consultório só aparece no turno em que teve agendamento.
    """
    consultorios: dict[str, tuple[LinhaConsultorioTurno, ...]] = {}
    agendas: dict[str, tuple[LinhaAgenda, ...]] = {}
    for turno in Turno:
        linhas_c: list[LinhaConsultorioTurno] = []
        linhas_a: list[LinhaAgenda] = []
        for grupo in grupos.values():
            do_turno = [x for x in grupo if x.turno is turno]
            if not do_turno:
                continue
            agenda = _linha_agenda(do_turno)
            linhas_a.append(agenda)
            linhas_c.append(
                LinhaConsultorioTurno(
                    id_agenda=agenda.id_agenda,
                    consultorio=agenda.consultorio,
                    agenda=agenda.agenda,
                    guiche=agenda.guiche,
                    atendimentos=agenda.atendimentos,
                    atendimentos_medidos=agenda.atendimentos_medidos,
                    tma_s=agenda.media_s,
                )
            )
        consultorios[turno.value] = tuple(sorted(linhas_c, key=_ordem_tma_turno))
        agendas[turno.value] = tuple(sorted(linhas_a, key=_ordem_agenda))
    return consultorios, agendas


def _chave_ordem_tma(linha: LinhaConsultorio) -> tuple[int, float, str]:
    tma = linha.total.tma_s
    return (1 if tma is None else 0, -(tma or 0), linha.consultorio.lower())


def calcular_metricas(
    agendamentos: Iterable[AgendamentoDoDia],
    agendas: Mapping[int, Agenda],
    regras: RegrasMetricas,
    inicio: datetime,
    fim: datetime,
    *,
    faltas_confirmadas: Iterable[int] = (),
    falhas_coleta: Iterable[FalhaColeta] = (),
    verificacao_faltas_indisponivel: bool = False,
) -> MetricasPeriodo:
    """Calcula todas as métricas do período ``[inicio, fim]``.

    ``agendamentos`` já deve estar filtrado pelas unidades e agendas incluídas no
    relatório. ``faltas_confirmadas`` traz os IDs que o SGG registra como falta (RF11):
    um agendamento que terminaria "sem baixa" e está nessa lista conta como falta.
    """
    confirmadas = frozenset(faltas_confirmadas)
    linhas: list[_Linha] = []
    reclassificadas: list[int] = []
    tem_guiche = any(a.guiche for a in agendas.values())
    for agendamento in agendamentos:
        linha, confirmada = _classificar(agendamento, agendas, regras, fim, confirmadas, tem_guiche)
        linhas.append(linha)
        if confirmada:
            reclassificadas.append(linha.id_agendamento)

    geral = _resumo_turno(linhas, "Total", "")
    por_turno = {
        turno.value: _resumo_turno(
            [x for x in linhas if x.turno is turno], turno.rotulo, regras.turnos.faixa(turno)
        )
        for turno in Turno
    }

    grupos: dict[str, list[_Linha]] = defaultdict(list)
    for linha in linhas:
        if linha.situacao is not Situacao.CANCELADO:
            grupos[linha.chave].append(linha)

    consultorios: list[LinhaConsultorio] = []
    tabela_agendas: list[LinhaAgenda] = []
    for grupo in grupos.values():
        referencia = grupo[0]
        consultorios.append(
            LinhaConsultorio(
                id_agenda=referencia.id_agenda,
                consultorio=referencia.consultorio,
                agenda=referencia.agenda,
                manha=_celula([x for x in grupo if x.turno is Turno.MANHA]),
                tarde=_celula([x for x in grupo if x.turno is Turno.TARDE]),
                total=_celula(grupo),
            )
        )
        tabela_agendas.append(_linha_agenda(grupo))
    consultorios.sort(key=_chave_ordem_tma)
    tabela_agendas.sort(key=_ordem_agenda)
    consultorios_por_turno, agendas_por_turno = _por_turno(grupos)
    por_turno_grupos = {
        nome: {
            turno.value: _resumo_turno(
                [x for x in linhas if x.turno is turno and x.guiche is guiche],
                turno.rotulo,
                regras.turnos.faixa(turno),
            )
            for turno in Turno
        }
        for nome, guiche in (("agendas", False), ("guiches", True))
    }

    atipicos = tuple(
        sorted(
            (
                AtendimentoAtipico(
                    id_agendamento=x.id_agendamento,
                    consultorio=x.consultorio,
                    hora_agendada=x.hora_agendada,
                    duracao_s=round(x.atendimento_s or 0),
                    motivo=x.atipico or "",
                )
                for x in linhas
                if x.atipico is not None
            ),
            key=lambda a: (a.hora_agendada or "99:99", a.id_agendamento),
        )
    )
    alertas = Alertas(
        atipicos=atipicos,
        sem_baixa=ListaIds.de(
            x.id_agendamento
            for x in linhas
            if x.situacao in (Situacao.AGENDADO, Situacao.AGUARDANDO)
        ),
        em_atendimento_aberto=ListaIds.de(
            x.id_agendamento for x in linhas if x.situacao is Situacao.EM_ATENDIMENTO
        ),
        sem_tempo_medido=ListaIds.de(
            x.id_agendamento
            for x in linhas
            if x.situacao is Situacao.ATENDIDO and x.atendimento_s is None
        ),
        faltas_confirmadas_na_agenda=ListaIds.de(reclassificadas),
        falhas_coleta=tuple(sorted(falhas_coleta, key=lambda f: f.inicio)),
        verificacao_faltas_indisponivel=verificacao_faltas_indisponivel,
    )

    por_grupo = {
        "agendas": _resumo_turno([x for x in linhas if not x.guiche], "Consultórios", ""),
        "guiches": _resumo_turno([x for x in linhas if x.guiche], "Guichês", ""),
    }
    consultorios_grupo, guiches_grupo = por_grupo["agendas"], por_grupo["guiches"]
    tem_guiche = guiches_grupo.agendados > 0
    kpis = Kpis(
        agendados=geral.agendados,
        cancelados=sum(1 for x in linhas if x.situacao is Situacao.CANCELADO),
        atendimentos=geral.atendimentos,
        faltas=geral.faltas,
        taxa_faltas=geral.taxa_faltas,
        sem_baixa=alertas.sem_baixa.total,
        espera_media_s=geral.espera_media_s,
        espera_mediana_s=geral.espera_mediana_s,
        espera_maxima_s=geral.espera_maxima_s,
        tma_s=geral.tma_s,
        atendimentos_medidos=geral.atendimentos_medidos,
        sem_turno=sum(
            1 for x in linhas if x.turno is None and x.situacao is not Situacao.CANCELADO
        ),
        atendimentos_consultorios=consultorios_grupo.atendimentos if tem_guiche else None,
        atendimentos_guiches=guiches_grupo.atendimentos if tem_guiche else None,
        espera_consultorio_s=consultorios_grupo.espera_media_s if tem_guiche else None,
        espera_recepcao_s=guiches_grupo.espera_media_s if tem_guiche else None,
        tma_consultorios_s=consultorios_grupo.tma_s if tem_guiche else None,
        tma_guiches_s=guiches_grupo.tma_s if tem_guiche else None,
    )

    return MetricasPeriodo(
        versao_regra=VERSAO_REGRA,
        inicio=inicio,
        fim=fim,
        limites_atipico_minutos=(_minutos(regras.atipico_min), _minutos(regras.atipico_max)),
        kpis=kpis,
        por_turno=por_turno,
        consultorios=tuple(consultorios),
        agendas=tuple(tabela_agendas),
        alertas=alertas,
        por_turno_grupos=por_turno_grupos,
        por_grupo=por_grupo,
        consultorios_por_turno=consultorios_por_turno,
        agendas_por_turno=agendas_por_turno,
    )
