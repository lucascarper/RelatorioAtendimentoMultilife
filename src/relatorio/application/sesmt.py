"""Relatório de gestão do SESMT: coleta nos endpoints do SGG e consolidação.

Roda no mesmo worker dos demais relatórios, com job e e-mail próprios. De madrugada (fora
da janela do coletor, que usa a cota da API) lê o SGG e grava ``resumo_sesmt``; o envio da
manhã só lê o resumo pronto (``EnviarRelatorio`` com o repositório e a lista do SESMT).

O SGG só responde documentos e eventos do eSocial **por empresa**, e a coleta respeita o
limite de requisições por minuto: são cerca de 1.500 consultas, o que leva mais de uma
hora. Para não gastar consulta à toa:

* documentos (PGR, PCMSO, LTCAT) só das empresas com contrato em andamento: sem contrato
  não há pendência a cobrar;
* eventos do eSocial só das empresas com o eSocial habilitado.

Uma empresa que falha na consulta não derruba a coleta: fica de fora e o e-mail avisa
que o relatório está parcial. Se a falha é geral (API fora, chave revogada), a coleta
aborta em vez de mandar um relatório vazio.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
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
        andamento: Callable[[str, int, int], None] | None = None,
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
            if andamento is not None:
                andamento(nome, posicao, len(ids))
        if ids and len(falharam) / len(ids) > FRACAO_MAXIMA_DE_FALHAS:
            raise ColetaSesmtInviavel(f"{len(falharam)} de {len(ids)} consultas de {nome} falharam")
        return itens, falharam

    def carregar(
        self,
        referencia: date,
        hoje: date,
        andamento: Callable[[str, int, int], None] | None = None,
    ) -> DadosSesmt:
        """``andamento(etapa, feitas, total)`` acompanha a coleta (exportação por período)."""
        empresas = {e.id: e for e in self._sgg.empresas_sesmt()}
        if andamento is not None:
            andamento("empresas", 1, 1)
        contratos = self._sgg.contratos_sesmt()
        if andamento is not None:
            andamento("contratos", 1, 1)
        com_contrato = sorted(
            {
                c.id_cliente
                for c in contratos
                if c.ultimo and c.situacao.casefold() == "em andamento" and c.id_cliente is not None
            }
        )
        com_esocial = sorted(e.id for e in empresas.values() if e.esocial_habilitado)
        documentos, sem_documentos = self._por_empresa(
            "documentos", com_contrato, self._sgg.documentos_sst, andamento
        )
        eventos, sem_eventos = self._por_empresa(
            "esocial", com_esocial, self._sgg.eventos_esocial, andamento
        )
        with self._uow() as uow:
            texto = uow.configuracoes.obter_todas().get(CHAVE_NOMES_GRUPOS)
        return DadosSesmt(
            referencia=referencia,
            hoje=hoje,
            empresas=empresas,
            contratos=contratos,
            documentos=documentos,
            eventos=eventos,
            nomes_grupos=ler_nomes_grupos(texto),
            empresas_sem_consulta=len(sem_documentos | sem_eventos),
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
            "a_vencer": resumo["a_vencer"]["total"],
            "vencidos": resumo["vencidos"]["total"],
            "eventos_esocial": resumo["esocial"]["total"],
            "empresas_sem_consulta": dados.empresas_sem_consulta,
        }

    def executar_periodo(
        self,
        dias: Sequence[date],
        andamento: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, object]:
        """Recalcula vários dias com **uma só** leitura do SGG.

        A API devolve todos os eventos do eSocial de cada empresa, então a mesma coleta
        serve a todos os dias; só a data de referência muda. Como no processamento de um dia,
        contratos e documentos valem pela situação de agora.
        """
        agora = self._relogio.agora()
        hoje = agora.astimezone(FUSO_BRASILIA).date()
        ultimo = max(dias)
        dados = self.carregar(ultimo, max(hoje, ultimo + timedelta(days=1)), andamento)
        with self._uow() as uow:
            for dia in dias:
                resumo = calcular_sesmt(replace(dados, referencia=dia), agora)
                uow.resumos_sesmt.salvar_metricas(dia, resumo, VERSAO_REGRA_SESMT, agora)
            uow.commit()
        return {
            "dias": len(dias),
            "empresas_sem_consulta": dados.empresas_sem_consulta,
        }

    def executar_agendado(
        self, referencia: date, tentativa: int, total: int = 3
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
