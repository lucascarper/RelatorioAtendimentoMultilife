"""Relatório de gestão do SESMT: planilhas de vencimento, eSocial do SGG e consolidação.

Roda no mesmo worker dos demais relatórios, com job e e-mail próprios. À noite (20:00,
depois do coletor, que usa a cota da API) grava ``resumo_sesmt``; o envio semanal (segunda,
08:00) só lê o resumo pronto (``EnviarRelatorio`` com o repositório e a lista do SESMT).

* **Documentos** (PGR, PCMSO, LTCAT): da última planilha "Vencimento(s) de ..." de cada
  tipo enviada no admin. A API do SGG não traz os documentos atuais (ADR 0021).
* **eSocial**: do SGG, uma consulta por empresa com o eSocial habilitado (sem lote nem
  filtro de data): cerca de 600 consultas, perto de 30 minutos.

Uma empresa que falha na consulta não derruba a coleta: fica de fora e o e-mail avisa
que o relatório está parcial. Se a falha é geral (API fora, chave revogada), a coleta
aborta em vez de mandar um relatório vazio.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from typing import TypeVar

import structlog

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.financeiro import formatar_nomes_centros, ler_nomes_centros
from relatorio.application.ports import ErroIntegracao, FabricaUoW, Relogio, SesmtGateway
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import (
    VERSAO_REGRA_SESMT,
    DadosSesmt,
    calcular_sesmt,
)

JOB_CONSOLIDAR_SESMT = "consolidar_sesmt"
CHAVE_NOMES_GRUPOS = "sesmt_grupos"
# Grupos de clientes cadastrados no SGG (a API de empresas só devolve o código do grupo).
GRUPOS_PADRAO = {
    "1": "GESTÃO PREMIUM",
    "2": "GESTÃO 2",
    "3": "GESTÃO 1",
    "4": "EVENTUAL",
    "5": "EVENTUAL COM E-SOCIAL",
    "6": "CREDENCIADOS",
    "7": "GESTÃO BASICO",
    "8": "DEMANDA FATURADA",
    "9": "CAGED",
    "10": "GESTÃO INTEGRAL",
    "11": "SEGMENTO",
    "14": "EM CANCELAMENTO",
}
FALHAS_SEGUIDAS_PARA_ABORTAR = 5
FRACAO_MAXIMA_DE_FALHAS = 0.10
REGISTRAR_A_CADA = 100

log = structlog.get_logger(__name__)
T = TypeVar("T")


def ler_nomes_grupos(texto: str | None) -> dict[str, str]:
    """Sem nada salvo no admin vale o padrão; "1=Premium; 2=Básico" → {"1": ..., "2": ...}."""
    return dict(GRUPOS_PADRAO) if texto is None else ler_nomes_centros(texto)


def formatar_nomes_grupos(nomes: dict[str, str]) -> str:
    return formatar_nomes_centros(nomes)


class ColetaSesmtInviavel(ErroIntegracao):
    """Falhas demais nas consultas por empresa: o relatório sairia com dados faltando."""


class ConsolidarSesmt:
    def __init__(
        self,
        sgg: SesmtGateway,
        uow: FabricaUoW,
        relogio: Relogio,
        alertar: AlertarTecnico,
    ) -> None:
        self._sgg = sgg
        self._uow = uow
        self._relogio = relogio
        self._alertar = alertar

    def _por_empresa(
        self,
        nome: str,
        ids: Sequence[int],
        consulta: Callable[[int], Iterable[T]],
    ) -> tuple[list[T], set[int]]:
        """Consulta empresa a empresa, tolerando falhas isoladas."""
        itens: list[T] = []
        falharam: set[int] = set()
        seguidas = 0
        for posicao, id_empresa in enumerate(ids, start=1):
            try:
                itens.extend(consulta(id_empresa))
                seguidas = 0
            except ErroIntegracao as erro:
                seguidas += 1
                falharam.add(id_empresa)
                log.warning(
                    "sesmt_consulta_falhou",
                    consulta=nome,
                    empresa=id_empresa,
                    erro=f"{type(erro).__name__}: {erro}",
                )
                if seguidas >= FALHAS_SEGUIDAS_PARA_ABORTAR:
                    raise
            if posicao % REGISTRAR_A_CADA == 0:
                log.info("sesmt_coleta_andamento", consulta=nome, feitas=posicao, total=len(ids))
        if ids and len(falharam) / len(ids) > FRACAO_MAXIMA_DE_FALHAS:
            raise ColetaSesmtInviavel(f"{len(falharam)} de {len(ids)} consultas de {nome} falharam")
        return itens, falharam

    def carregar(self, referencia: date, hoje: date) -> DadosSesmt:
        empresas = {e.id: e for e in self._sgg.empresas_sesmt()}
        com_esocial = sorted(e.id for e in empresas.values() if e.esocial_habilitado)
        eventos, sem_eventos = self._por_empresa("esocial", com_esocial, self._sgg.eventos_esocial)
        with self._uow() as uow:
            texto = uow.configuracoes.obter_todas().get(CHAVE_NOMES_GRUPOS)
            planilhas = uow.planilhas_sesmt.listar()
        return DadosSesmt(
            referencia=referencia,
            hoje=hoje,
            empresas=empresas,
            documentos=[d for p in planilhas for d in p.documentos],
            eventos=eventos,
            nomes_grupos=ler_nomes_grupos(texto),
            empresas_sem_consulta=len(sem_eventos),
            planilhas={p.tipo: p.enviada_em for p in planilhas},
        )

    def executar(self, referencia: date) -> dict[str, object]:
        agora = self._relogio.agora()
        hoje = agora.astimezone(FUSO_BRASILIA).date()
        dados = self.carregar(referencia, max(hoje, referencia + timedelta(days=1)))
        resumo = calcular_sesmt(dados, agora)
        with self._uow() as uow:
            uow.resumos_sesmt.salvar_metricas(referencia, resumo, VERSAO_REGRA_SESMT, agora)
            uow.commit()
        return {
            "referencia": referencia.isoformat(),
            "proximo_mes": resumo["proximo_mes"]["total"],
            "vencidos": resumo["vencidos"]["total"],
            "esocial_rejeitados": resumo["esocial"]["rejeitados"],
            "empresas_sem_consulta": dados.empresas_sem_consulta,
        }

    def executar_agendado(
        self, referencia: date, tentativa: int, total: int = 2
    ) -> dict[str, object]:
        try:
            return self.executar(referencia)
        except Exception as erro:
            if tentativa >= total:
                self._alertar.enviar(
                    "Relatório do SESMT não consolidado",
                    f"A coleta do SESMT de {referencia:%d/%m/%Y} falhou nas {total} tentativas."
                    " O e-mail da manhã não sai sem o resumo.",
                    {"Data": f"{referencia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
            raise
