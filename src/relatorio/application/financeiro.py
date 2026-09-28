"""Relatório financeiro diário: coleta nos endpoints financeiros do SGG e consolidação.

Roda no mesmo worker do relatório de atendimentos, com job e e-mail próprios. Às 05:45
(retentativas 06:30 e 07:15) lê o SGG e grava ``resumo_financeiro``; o envio das 07:59
só lê o resumo pronto (``EnviarRelatorio`` com o repositório e a lista do financeiro).

A referência é o dia anterior (caixa e faturamento). Projeções e contratos são a foto
do momento da coleta: a API devolve a situação atual dos títulos, então reprocessar
uma data antiga mostra esses blocos como estão hoje.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.ports import FabricaUoW, FinanceiroGateway, Relogio
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.financeiro import (
    HORIZONTES_PROJECAO,
    JANELA_CONTRATOS_DIAS,
    VERSAO_REGRA_FINANCEIRO,
    DadosFinanceiros,
    calcular_financeiro,
    inicio_do_mes,
    inicio_janela_emissao,
    servicos_para_margem,
)

JOB_CONSOLIDAR_FINANCEIRO = "consolidar_financeiro"
CHAVE_NOMES_CENTROS = "financeiro_centros_custo"

log = structlog.get_logger(__name__)


def ler_nomes_centros(texto: str) -> dict[str, str]:
    """ "2=Clínica; 3=Ocupacional" → {"2": "Clínica", "3": "Ocupacional"}."""
    nomes: dict[str, str] = {}
    for parte in texto.replace("\n", ";").split(";"):
        codigo, _, nome = parte.partition("=")
        if codigo.strip() and nome.strip():
            nomes[codigo.strip()] = " ".join(nome.split())
    return nomes


def formatar_nomes_centros(nomes: dict[str, str]) -> str:
    return "; ".join(f"{codigo}={nome}" for codigo, nome in sorted(nomes.items()))


class ConsolidarFinanceiro:
    def __init__(
        self,
        sgg: FinanceiroGateway,
        uow: FabricaUoW,
        relogio: Relogio,
        alertar: AlertarTecnico,
    ) -> None:
        self._sgg = sgg
        self._uow = uow
        self._relogio = relogio
        self._alertar = alertar

    def carregar(self, referencia: date, hoje: date) -> DadosFinanceiros:
        """Lê o SGG: ~40 requisições, divididas em janelas de até 31 dias."""
        mes = inicio_do_mes(referencia)
        ate_projecao = hoje + timedelta(days=max(*HORIZONTES_PROJECAO, JANELA_CONTRATOS_DIAS))
        emitidos = self._sgg.receber_emitidos(inicio_janela_emissao(referencia), referencia)
        do_mes = [t for t in emitidos if t.emissao and mes <= t.emissao <= referencia]
        precos = {s: self._sgg.precos_servico(s) for s in servicos_para_margem(do_mes)}
        with self._uow() as uow:
            nomes = ler_nomes_centros(uow.configuracoes.obter_todas().get(CHAVE_NOMES_CENTROS, ""))
        return DadosFinanceiros(
            referencia=referencia,
            hoje=hoje,
            recebidos_mes=self._sgg.receber_pagos(mes, referencia),
            pagos_mes=self._sgg.pagar_pagos(mes, referencia),
            emitidos=emitidos,
            a_receber=self._sgg.receber_a_vencer(hoje, ate_projecao),
            a_pagar=self._sgg.pagar_a_vencer(hoje, ate_projecao),
            contratos=self._sgg.contratos_ativos(),
            precos=precos,
            nomes_centros=nomes,
        )

    def executar(self, referencia: date) -> dict[str, object]:
        agora = self._relogio.agora()
        hoje = agora.astimezone(FUSO_BRASILIA).date()
        dados = self.carregar(referencia, max(hoje, referencia + timedelta(days=1)))
        with self._uow() as uow:
            anterior = uow.resumos_financeiros.obter(referencia - timedelta(days=1))
        resumo = calcular_financeiro(dados, agora, anterior.metricas if anterior else None)
        with self._uow() as uow:
            uow.resumos_financeiros.salvar_metricas(
                referencia, resumo, VERSAO_REGRA_FINANCEIRO, agora
            )
            uow.commit()
        return {
            "referencia": referencia.isoformat(),
            "saldo_dia": resumo["caixa"]["saldo_dia"],
            "faturamento_mes": resumo["faturamento"]["mes"],
            "contratos_ativos": resumo["recorrente"]["contratos_ativos"],
        }

    def executar_agendado(
        self, referencia: date, tentativa: int, total: int = 3
    ) -> dict[str, object]:
        try:
            return self.executar(referencia)
        except Exception as erro:
            if tentativa >= total:
                self._alertar.enviar(
                    "Relatório financeiro não consolidado",
                    f"A coleta financeira de {referencia:%d/%m/%Y} falhou nas {total} tentativas."
                    " O e-mail das 07:59 não sai sem o resumo.",
                    {"Data": f"{referencia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
            raise
