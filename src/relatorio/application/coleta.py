"""Coletor de eventos (compartilhado com o Painel de Tempo de Atendimento).

Casos de uso que escrevem o log ``agendamento_evento``:

* ``ColetarCiclo`` — polling incremental a cada ``COLETA_INTERVALO_S`` (5 s) no expediente;
* ``ReconciliarDia`` — varredura do dia inteiro às 18:30 para pegar o que escapou;
* ``SincronizarAgendas`` — cadastro de agendas/consultórios (nomes e unidades).

O coletor não depende de nada do relatório: se o painel tiver o próprio coletor, basta
desligar este (``COLETOR_HABILITADO=false``) e o relatório passa a só ler a tabela.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

import structlog

from relatorio.application.configuracao import inicio_do_dia
from relatorio.application.ports import FabricaUoW, Relogio, SggGateway, UnidadeDeTrabalho
from relatorio.domain.deteccao import detectar_transicao
from relatorio.domain.entidades import (
    FUSO_BRASILIA,
    Agenda,
    AgendamentoSgg,
    Evento,
    OrigemEvento,
    Situacao,
)

log = structlog.get_logger(__name__)

CURSOR_AGENDAMENTOS = "agendamentos_editados"
SOBREPOSICAO_CURSOR = timedelta(minutes=2)
RETROCESSO_MAXIMO = timedelta(days=7)


def _chave_nome(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


class ResolvedorAgendas:
    """Liga o nome da agenda (que vem no agendamento) ao ``id_agenda`` do cadastro."""

    def __init__(self, agendas: Iterable[Agenda]) -> None:
        self._por_nome_unidade: dict[tuple[str, int | None], int] = {}
        por_nome: dict[str, set[int]] = {}
        for agenda in agendas:
            chave = _chave_nome(agenda.nome)
            self._por_nome_unidade[(chave, agenda.id_unidade_atendimento)] = agenda.id_agenda
            por_nome.setdefault(chave, set()).add(agenda.id_agenda)
        # Só resolve pelo nome sozinho quando não há ambiguidade entre unidades.
        self._por_nome = {nome: next(iter(ids)) for nome, ids in por_nome.items() if len(ids) == 1}

    def resolver(self, nome: str, id_unidade: int | None) -> int | None:
        chave = _chave_nome(nome)
        return self._por_nome_unidade.get((chave, id_unidade)) or self._por_nome.get(chave)


@dataclass(frozen=True, slots=True)
class ResultadoRegistro:
    registros: int
    eventos_novos: int
    saltos: int
    snapshots_atualizados: int


def registrar_observacoes(
    uow: UnidadeDeTrabalho,
    registros: Sequence[AgendamentoSgg],
    observado_em: datetime,
    origem: OrigemEvento,
) -> ResultadoRegistro:
    """Aplica a detecção de transição a cada registro, dentro da transação recebida."""
    resolvedor = ResolvedorAgendas(uow.agendas.listar())
    snapshots = uow.snapshots.obter_varios({r.id_agendamento for r in registros})
    eventos_novos = saltos = atualizados = 0

    # Processa na ordem das edições: se o mesmo agendamento vier duas vezes no lote,
    # a segunda leitura já compara com o snapshot atualizado pela primeira.
    ordenados = sorted(
        registros,
        key=lambda r: (r.data_hora_edicao or observado_em, r.id_agendamento),
    )
    for registro in ordenados:
        deteccao = detectar_transicao(
            registro,
            snapshots.get(registro.id_agendamento),
            id_agenda=resolvedor.resolver(registro.agenda_nome, registro.id_unidade_atendimento),
            observado_em=observado_em,
            origem=origem,
        )
        if deteccao.snapshot is not None:
            uow.snapshots.salvar(deteccao.snapshot)
            snapshots[registro.id_agendamento] = deteccao.snapshot
            atualizados += 1
        if deteccao.evento is not None and uow.eventos.inserir(deteccao.evento):
            eventos_novos += 1
            saltos += int(deteccao.salto)
    return ResultadoRegistro(
        registros=len(registros),
        eventos_novos=eventos_novos,
        saltos=saltos,
        snapshots_atualizados=atualizados,
    )


class ColetarCiclo:
    """RF01/RF02 — polling incremental por ``editado_aPartirDe``/``editado_ate``.

    O cursor fica no banco (RNF04): após um reinício, o coletor retoma de onde parou.
    Cada ciclo relê 2 minutos antes do último ``editado_ate`` para não perder edições
    no limite; duplicatas são descartadas pela detecção e pelo UNIQUE do banco.
    """

    def __init__(self, sgg: SggGateway, uow: FabricaUoW, relogio: Relogio) -> None:
        self._sgg = sgg
        self._uow = uow
        self._relogio = relogio

    def executar(self) -> dict[str, object]:
        agora = self._relogio.agora()
        with self._uow() as uow:
            cursor = uow.cursores.obter(CURSOR_AGENDAMENTOS)
        if cursor is None:
            de = inicio_do_dia(agora.astimezone(FUSO_BRASILIA).date())
        else:
            de = cursor - SOBREPOSICAO_CURSOR
        de = max(de, agora - RETROCESSO_MAXIMO)

        antes = self._sgg.requisicoes_realizadas
        registros = self._sgg.agendamentos_editados(de, agora)
        with self._uow() as uow:
            resultado = registrar_observacoes(uow, registros, agora, OrigemEvento.POLLING)
            uow.cursores.salvar(CURSOR_AGENDAMENTOS, agora)
            uow.commit()
        return {
            "de": de.isoformat(),
            "ate": agora.isoformat(),
            "requisicoes": self._sgg.requisicoes_realizadas - antes,
            "registros": resultado.registros,
            "eventos_novos": resultado.eventos_novos,
            "saltos": resultado.saltos,
        }


SITUACOES_EM_ABERTO = frozenset({Situacao.AGENDADO, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO})
MAXIMO_IDS_NO_DETALHE = 20


def fechar_fora_do_dia(
    uow: UnidadeDeTrabalho,
    dia: date,
    presentes: Collection[int],
    observado_em: datetime,
) -> list[int]:
    """Fecha os agendamentos do dia, ainda em aberto aqui, que sumiram da lista do SGG.

    Quem é remarcado para outro dia ou excluído deixa de vir na consulta do dia, e o
    polling por edição nunca mais o vê: sem isso ele ficaria "Aguardando" para sempre
    na fila do monitor. Vira "Cancelado" (fora das contas do dia) com origem
    reconciliação. Se ele voltar a aparecer, a detecção registra a nova situação.
    O mesmo vale para quem veio com uma situação que o sistema não conhece.
    """
    fechados = []
    for snapshot in uow.snapshots.listar_por_data(dia, dia):
        if (
            snapshot.situacao_atual not in SITUACOES_EM_ABERTO
            or snapshot.id_agendamento in presentes
        ):
            continue
        uow.eventos.inserir(
            Evento(
                id_agendamento=snapshot.id_agendamento,
                status_anterior=snapshot.situacao_atual,
                status_novo=Situacao.CANCELADO,
                ocorrido_em=observado_em,
                observado_em=observado_em,
                origem=OrigemEvento.RECONCILIACAO,
            )
        )
        uow.snapshots.salvar(
            replace(snapshot, situacao_atual=Situacao.CANCELADO, atualizado_em=observado_em)
        )
        fechados.append(snapshot.id_agendamento)
    return fechados


class ReconciliarDia:
    """RF03: varredura do dia inteiro por ``data_hora_agendamento``.

    Roda às 18:30 e, durante o expediente, a cada poucos minutos: o polling só enxerga
    o que teve a data de edição alterada, e a varredura acerta o que escapou.
    """

    def __init__(self, sgg: SggGateway, uow: FabricaUoW, relogio: Relogio) -> None:
        self._sgg = sgg
        self._uow = uow
        self._relogio = relogio

    def executar(self, dia: date) -> dict[str, object]:
        antes = self._sgg.requisicoes_realizadas
        registros = self._sgg.agendamentos_do_dia(dia)
        ignorados = dict(self._sgg.ignorados_na_ultima_consulta)
        agora = self._relogio.agora()
        with self._uow() as uow:
            resultado = registrar_observacoes(uow, registros, agora, OrigemEvento.RECONCILIACAO)
            fechados: list[int] = []
            # Lista vazia quase sempre é falha da API: nunca fecha o dia inteiro por isso.
            # Quem veio com situação desconhecida (ex.: "Remarcado") também sai da fila:
            # seja qual for a situação nova, não é mais "Aguardando". O motivo fica no
            # detalhe da execução para a situação ser mapeada.
            if registros:
                presentes = {r.id_agendamento for r in registros}
                fechados = fechar_fora_do_dia(uow, dia, presentes, agora)
            uow.commit()
        if fechados:
            log.info("agendamentos_fora_do_dia", referencia=dia.isoformat(), ids=fechados)
        detalhe: dict[str, object] = {
            "referencia": dia.isoformat(),
            "requisicoes": self._sgg.requisicoes_realizadas - antes,
            "registros": resultado.registros,
            "eventos_novos": resultado.eventos_novos,
            "saltos": resultado.saltos,
        }
        if fechados:
            detalhe["fora_do_dia"] = len(fechados)
            detalhe["ids_fora_do_dia"] = ", ".join(map(str, fechados[:MAXIMO_IDS_NO_DETALHE]))
        if ignorados:
            detalhe["ignorados"] = len(ignorados)
            detalhe["motivos_ignorados"] = "; ".join(sorted(set(ignorados.values())))
        return detalhe


class SincronizarAgendas:
    """RF06 — atualiza o cadastro de agendas (consultório = campo ``sala``)."""

    def __init__(self, sgg: SggGateway, uow: FabricaUoW) -> None:
        self._sgg = sgg
        self._uow = uow

    def executar(self) -> dict[str, object]:
        agendas = self._sgg.agendas()
        if not agendas:
            # Resposta vazia quase sempre é problema na API; manter o cadastro anterior
            # evita desativar todas as agendas por engano.
            return {"agendas": 0, "novas": 0, "atualizadas": 0, "desativadas": 0}
        with self._uow() as uow:
            novas, atualizadas, desativadas = uow.agendas.sincronizar(agendas)
            uow.commit()
        return {
            "agendas": len(agendas),
            "novas": novas,
            "atualizadas": atualizadas,
            "desativadas": desativadas,
        }
