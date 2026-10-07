"""Exportação de relatórios por período (planilha com o relatório e os dados fonte).

``solicitar`` valida o pedido e grava a exportação na fila; ``executar`` (em segundo plano,
logo depois da resposta da tela) chama o exportador do tipo, grava o andamento a cada passo
(a barra da tela lê esse andamento) e guarda o arquivo por 24 horas para download.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Protocol

import structlog

from relatorio.application.modelos import Exportacao, StatusExportacao
from relatorio.application.ports import ErroIntegracao, FabricaUoW, Relogio
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.exportacao import (
    ACOES,
    EXPORTAR,
    PROCESSAR,
    ROTULOS,
    SESMT,
    SESMT_FIM_HORA,
    SESMT_INICIO_HORA,
    TIPOS,
    Planilha,
    sesmt_liberado,
    validar_periodo,
)

GUARDA_ARQUIVO = timedelta(hours=24)

log = structlog.get_logger(__name__)

Progresso = Callable[[int, str], None]
"""Recebe o percentual (0 a 100) e o que está sendo feito, para a barra da tela."""


class Exportador(Protocol):
    def gerar(self, inicio: date, fim: date, progresso: Progresso) -> Planilha: ...


class ProcessadorPeriodo(Protocol):
    def processar(
        self, inicio: date, fim: date, opcoes: frozenset[str], progresso: Progresso
    ) -> str:
        """Recalcula os dias do período e devolve a frase final para a tela."""
        ...


class EscritorPlanilha(Protocol):
    def escrever(self, planilha: Planilha) -> tuple[str, bytes]:
        """Devolve o nome do arquivo e o conteúdo .xlsx."""
        ...


class ExportarRelatorio:
    def __init__(
        self,
        uow: FabricaUoW,
        relogio: Relogio,
        exportadores: Mapping[str, Exportador],
        escritor: EscritorPlanilha,
        processadores: Mapping[str, ProcessadorPeriodo] | None = None,
    ) -> None:
        self._uow = uow
        self._relogio = relogio
        self._exportadores = exportadores
        self._escritor = escritor
        self._processadores = processadores or {}

    def solicitar(
        self,
        tipo: str,
        inicio: date,
        fim: date,
        solicitante: str,
        acao: str = EXPORTAR,
        opcoes: str = "",
    ) -> Exportacao:
        """Valida e põe na fila. Lança ``ValueError`` com a mensagem para a tela."""
        disponiveis = self._processadores if acao == PROCESSAR else self._exportadores
        if acao not in ACOES or tipo not in TIPOS or tipo not in disponiveis:
            raise ValueError("Relatório desconhecido.")
        agora = self._relogio.agora()
        validar_periodo(inicio, fim, agora.astimezone(FUSO_BRASILIA).date(), tipo, acao)
        if tipo == SESMT and not sesmt_liberado(agora):
            o_que = "O processamento" if acao == PROCESSAR else "A exportação"
            raise ValueError(
                f"{o_que} do SESMT relê o SGG empresa por empresa e só roda das "
                f"{SESMT_INICIO_HORA}h às {SESMT_FIM_HORA}h."
            )
        exportacao = Exportacao(
            id=uuid.uuid4().hex,
            tipo=tipo,
            inicio=inicio,
            fim=fim,
            solicitante=solicitante,
            criado_em=agora,
            etapa="Na fila",
            acao=acao,
            opcoes=opcoes,
        )
        with self._uow() as uow:
            uow.exportacoes.apagar_anteriores_a(agora - GUARDA_ARQUIVO)
            uow.exportacoes.criar(exportacao)
            uow.commit()
        log.info("exportacao_solicitada", id=exportacao.id, tipo=tipo, acao=acao, por=solicitante)
        return exportacao

    def ultimas(self, solicitante: str) -> dict[str, Exportacao]:
        """Para a página mostrar a exportação em andamento (ou pronta) ao ser reaberta."""
        desde = self._relogio.agora() - GUARDA_ARQUIVO
        with self._uow() as uow:
            return uow.exportacoes.ultimas(solicitante, desde)

    def obter(self, id_exportacao: str) -> Exportacao | None:
        with self._uow() as uow:
            return uow.exportacoes.obter(id_exportacao)

    def _progresso(self, id_exportacao: str) -> Progresso:
        ultimo = {"valor": -1, "etapa": ""}

        def registrar(valor: int, etapa: str) -> None:
            # Só grava quando muda (as coletas chamam isto a cada consulta ao SGG).
            if valor == ultimo["valor"] and etapa == ultimo["etapa"]:
                return
            ultimo["valor"], ultimo["etapa"] = valor, etapa
            with self._uow() as uow:
                uow.exportacoes.progresso(id_exportacao, valor, etapa)
                uow.commit()

        return registrar

    def executar(self, id_exportacao: str) -> dict[str, object]:
        exportacao = self.obter(id_exportacao)
        if exportacao is None or exportacao.status is not StatusExportacao.FILA:
            return {"exportacao": id_exportacao, "status": "ignorada"}
        progresso = self._progresso(id_exportacao)
        progresso(1, "Começando")
        if exportacao.acao == PROCESSAR:
            return self._processar(exportacao, progresso)
        try:
            planilha = self._exportadores[exportacao.tipo].gerar(
                exportacao.inicio, exportacao.fim, progresso
            )
            progresso(97, "Montando a planilha")
            nome, conteudo = self._escritor.escrever(planilha)
        except Exception as erro:
            mensagem = (
                f"O SGG não respondeu: {erro}"
                if isinstance(erro, ErroIntegracao)
                else "Erro inesperado ao gerar a planilha. Tente de novo; se persistir, avise a TI."
            )
            with self._uow() as uow:
                uow.exportacoes.falhar(id_exportacao, mensagem)
                uow.commit()
            log.exception("exportacao_falhou", id=id_exportacao, tipo=exportacao.tipo)
            raise
        with self._uow() as uow:
            uow.exportacoes.concluir(id_exportacao, nome, conteudo)
            uow.commit()
        return {
            "exportacao": id_exportacao,
            "tipo": ROTULOS[exportacao.tipo],
            "periodo": f"{exportacao.inicio:%d/%m/%Y} a {exportacao.fim:%d/%m/%Y}",
            "abas": len(planilha.abas),
            "linhas": sum(len(a.linhas) for a in planilha.abas),
            "bytes": len(conteudo),
        }

    def _processar(self, exportacao: Exportacao, progresso: Progresso) -> dict[str, object]:
        opcoes = frozenset(o for o in exportacao.opcoes.split(",") if o)
        try:
            frase = self._processadores[exportacao.tipo].processar(
                exportacao.inicio, exportacao.fim, opcoes, progresso
            )
        except Exception as erro:
            mensagem = (
                f"O SGG não respondeu: {erro}"
                if isinstance(erro, ErroIntegracao)
                else "Erro inesperado ao processar o período. Tente de novo; se persistir, "
                "avise a TI."
            )
            with self._uow() as uow:
                uow.exportacoes.falhar(exportacao.id, mensagem)
                uow.commit()
            log.exception("processamento_periodo_falhou", id=exportacao.id, tipo=exportacao.tipo)
            raise
        with self._uow() as uow:
            uow.exportacoes.concluir(exportacao.id, "", None, etapa=frase)
            uow.commit()
        return {
            "exportacao": exportacao.id,
            "tipo": ROTULOS[exportacao.tipo],
            "periodo": f"{exportacao.inicio:%d/%m/%Y} a {exportacao.fim:%d/%m/%Y}",
            "resultado": frase,
        }

    def marcar_ocupado(self, id_exportacao: str) -> None:
        """A trava do job estava ocupada (outra exportação ou a coleta noturna em andamento)."""
        with self._uow() as uow:
            uow.exportacoes.falhar(
                id_exportacao,
                "Outra exportação deste relatório (ou a coleta noturna) está em andamento. "
                "Tente de novo em alguns minutos.",
            )
            uow.commit()
