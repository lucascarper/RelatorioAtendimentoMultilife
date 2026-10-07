"""Processamento por período: recalcula os resumos de vários dias de uma vez.

É o "Reprocessar uma data" dos relatórios estendido a um intervalo, com a mesma barra de
andamento da exportação. **Não reenvia e-mail**: reenviar um período inteiro mandaria um
e-mail por dia aos destinatários. Para reenviar, use a data única do relatório.

* Atendimento: recalcula cada dia a partir dos eventos gravados (opcionalmente relê o dia
  inteiro no SGG antes, o que é lento).
* Financeiro: relê o SGG e recalcula cada dia (cerca de 1 minuto por dia).
* SESMT: uma única leitura do SGG (mais de uma hora) serve a todos os dias.
* Atendimentos por médico: relê os exames clínicos de cada dia.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

import structlog

from relatorio.application.exames import ProcessarExames
from relatorio.application.exportacao import Progresso
from relatorio.application.financeiro import ConsolidarFinanceiro
from relatorio.application.manutencao import ReprocessarData
from relatorio.application.ports import ErroIntegracao
from relatorio.application.sesmt import ConsolidarSesmt
from relatorio.domain.exportacao import ATENDIMENTO, FINANCEIRO, MEDICOS, SESMT, dias

OPCAO_RECONCILIAR = "reconciliar"

log = structlog.get_logger(__name__)


class Processador:
    """Contrato: processa de ``inicio`` a ``fim`` e devolve a frase final para a tela."""

    def processar(
        self, inicio: date, fim: date, opcoes: frozenset[str], progresso: Progresso
    ) -> str:
        raise NotImplementedError


def _frase(feitos: int, falhas: list[date]) -> str:
    texto = f"{feitos} dia(s) processado(s)"
    if falhas:
        lista = ", ".join(f"{d:%d/%m}" for d in falhas[:10])
        texto += f"; sem resposta do SGG em {len(falhas)}: {lista}"
    return texto


def _por_dia(
    inicio: date,
    fim: date,
    progresso: Progresso,
    rotulo: str,
    um_dia: Callable[[date], object],
) -> str:
    """Processa dia a dia; um dia que falha no SGG não impede os outros (só todos falharem)."""
    lista = dias(inicio, fim)
    falhas: list[date] = []
    for posicao, dia in enumerate(lista):
        progresso(
            2 + 96 * posicao // len(lista),
            f"{rotulo} {dia:%d/%m/%Y} ({posicao + 1} de {len(lista)})",
        )
        try:
            um_dia(dia)
        except ErroIntegracao as erro:
            log.warning("processamento_periodo_dia_falhou", dia=dia.isoformat(), erro=str(erro))
            falhas.append(dia)
    if len(falhas) == len(lista):
        raise ErroIntegracao("O SGG não respondeu em nenhum dia do período.")
    return _frase(len(lista) - len(falhas), falhas)


class ProcessarAtendimento(Processador):
    def __init__(self, reprocessar: ReprocessarData) -> None:
        self._reprocessar = reprocessar

    def processar(
        self, inicio: date, fim: date, opcoes: frozenset[str], progresso: Progresso
    ) -> str:
        reconciliar = OPCAO_RECONCILIAR in opcoes
        return _por_dia(
            inicio,
            fim,
            progresso,
            "Recalculando" + (" e relendo o SGG:" if reconciliar else ":"),
            lambda dia: self._reprocessar.executar(dia, reconciliar=reconciliar, enviar=False),
        )


class ProcessarFinanceiro(Processador):
    def __init__(self, consolidar: ConsolidarFinanceiro) -> None:
        self._consolidar = consolidar

    def processar(
        self, inicio: date, fim: date, opcoes: frozenset[str], progresso: Progresso
    ) -> str:
        return _por_dia(
            inicio, fim, progresso, "Lendo o SGG e recalculando", self._consolidar.executar
        )


class ProcessarMedicos(Processador):
    def __init__(self, processar: ProcessarExames) -> None:
        self._processar = processar

    def processar(
        self, inicio: date, fim: date, opcoes: frozenset[str], progresso: Progresso
    ) -> str:
        return _por_dia(
            inicio, fim, progresso, "Lendo os exames clínicos de", self._processar.executar
        )


class ProcessarSesmt(Processador):
    def __init__(self, consolidar: ConsolidarSesmt) -> None:
        self._consolidar = consolidar

    def processar(
        self, inicio: date, fim: date, opcoes: frozenset[str], progresso: Progresso
    ) -> str:
        faixas = {
            "empresas": (3, 3),
            "contratos": (6, 6),
            "documentos": (6, 55),
            "esocial": (55, 93),
        }
        rotulos = {
            "empresas": "Relendo as empresas no SGG",
            "contratos": "Relendo os contratos no SGG",
            "documentos": "Relendo programas e laudos, empresa por empresa",
            "esocial": "Relendo os eventos do eSocial, empresa por empresa",
        }

        def andamento(etapa: str, feitas: int, total: int) -> None:
            de, ate = faixas[etapa]
            valor = de + (ate - de) * feitas // max(total, 1)
            progresso(valor, rotulos[etapa] + (f" ({feitas} de {total})" if total > 1 else ""))

        lista: Sequence[date] = dias(inicio, fim)
        progresso(2, "Começando")
        detalhe = self._consolidar.executar_periodo(lista, andamento)
        progresso(97, "Gravando os resumos")
        extra = detalhe["empresas_sem_consulta"]
        return f"{len(lista)} dia(s) processado(s)" + (
            f"; {extra} empresa(s) sem resposta do SGG" if extra else ""
        )


def montar_processadores(
    reprocessar: ReprocessarData,
    consolidar_financeiro: ConsolidarFinanceiro | None,
    consolidar_sesmt: ConsolidarSesmt | None,
    processar_exames: ProcessarExames | None,
) -> dict[str, Processador]:
    processadores: dict[str, Processador] = {ATENDIMENTO: ProcessarAtendimento(reprocessar)}
    if consolidar_financeiro is not None:
        processadores[FINANCEIRO] = ProcessarFinanceiro(consolidar_financeiro)
    if consolidar_sesmt is not None:
        processadores[SESMT] = ProcessarSesmt(consolidar_sesmt)
    if processar_exames is not None:
        processadores[MEDICOS] = ProcessarMedicos(processar_exames)
    return processadores
