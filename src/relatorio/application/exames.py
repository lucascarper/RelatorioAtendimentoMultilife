"""Atendimentos por médico: processamento dos exames clínicos e complemento do e-mail.

* ``ProcessarExames`` (23:20, retentativas 02:20 e 05:20, ou manual no admin): uma consulta
  ao SGG traz os exames clínicos do dia. Todos os médicos que aparecem são registrados (para
  a lista de escolha em Configurações); só os exames dos médicos escolhidos são gravados,
  com o nome da empresa (consultado uma vez e reaproveitado nos dias seguintes).
* ``ComplementoExames`` (no envio do relatório de atendimentos): soma ao e-mail a seção
  com as contagens por médico e anexa a planilha com a lista nominal. Os nomes dos
  trabalhadores são lidos do SGG nesse momento e não são gravados (LGPD, ADR 0015).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Protocol

import structlog

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.modelos import Anexo, ColetaExames
from relatorio.application.ports import ErroIntegracao, ExamesGateway, FabricaUoW, Relogio
from relatorio.domain.exames import (
    ExameClinico,
    Medico,
    linhas_da_planilha,
    resumo_por_medico,
)

JOB_PROCESSAR_EXAMES = "processar_exames"
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

log = structlog.get_logger(__name__)


class GeradorPlanilha(Protocol):
    def atendimentos_por_medico(
        self, dia: date, abas: Sequence[tuple[Medico, Sequence[ExameClinico]]]
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class Complemento:
    """O que um relatório acrescenta ao e-mail de outro: métricas extras e anexos.

    Os anexos vão só para os destinatários autorizados; os demais recebem o e-mail com
    ``metricas_sem_anexo`` (que avisam que a lista é restrita).
    """

    metricas: Mapping[str, Any]
    anexos: tuple[Anexo, ...] = ()
    metricas_sem_anexo: Mapping[str, Any] | None = None

    def para_quem_nao_recebe_anexo(self) -> Mapping[str, Any]:
        return self.metricas if self.metricas_sem_anexo is None else self.metricas_sem_anexo


class ProcessarExames:
    def __init__(
        self, sgg: ExamesGateway, uow: FabricaUoW, relogio: Relogio, alertar: AlertarTecnico
    ) -> None:
        self._sgg = sgg
        self._uow = uow
        self._relogio = relogio
        self._alertar = alertar

    def executar(self, dia: date) -> dict[str, object]:
        exames = self._sgg.exames_clinicos(dia)
        vistos = {e.crm: Medico(e.crm, e.medico) for e in exames}
        with self._uow() as uow:
            uow.medicos.registrar_vistos(list(vistos.values()), dia)
            escolhidos = {m.crm for m in uow.medicos.selecionados()}
            do_relatorio = [e for e in exames if e.crm in escolhidos]
            ids_empresas = {e.id_empresa for e in do_relatorio}
            nomes = uow.exames.nomes_empresas(ids_empresas)
            uow.commit()

        consultadas = falhas = 0
        for id_empresa in sorted(ids_empresas - nomes.keys()):
            consultadas += 1
            try:
                empresa = self._sgg.empresa(id_empresa)
            except ErroIntegracao as erro:
                # Uma empresa sem nome não impede o relatório: aparece pelo código.
                falhas += 1
                log.warning("exames_empresa_sem_nome", empresa=id_empresa, erro=str(erro))
                empresa = None
            nomes[id_empresa] = empresa.nome if empresa else f"Empresa #{id_empresa}"

        gravar = [replace(e, empresa=nomes[e.id_empresa], funcionario="") for e in do_relatorio]
        with self._uow() as uow:
            uow.exames.substituir_dia(dia, gravar)
            uow.exames.registrar_coleta(
                ColetaExames(
                    data=dia,
                    processado_em=self._relogio.agora(),
                    clinicos=len(exames),
                    selecionados=len(gravar),
                    medicos=tuple(sorted(escolhidos)),
                )
            )
            uow.commit()
        return {
            "referencia": dia.isoformat(),
            "clinicos": len(exames),
            "selecionados": len(gravar),
            "medicos_vistos": len(vistos),
            "medicos_escolhidos": len(escolhidos),
            "empresas_consultadas": consultadas,
            "empresas_sem_nome": falhas,
        }

    def executar_agendado(self, dia: date, tentativa: int, total: int = 3) -> dict[str, object]:
        try:
            return self.executar(dia)
        except Exception as erro:
            if tentativa >= total:
                self._alertar.enviar(
                    "Atendimentos por médico não processados",
                    f"Os exames clínicos de {dia:%d/%m/%Y} não puderam ser lidos do SGG nas "
                    f"{total} tentativas. O envio da manhã tenta mais uma vez.",
                    {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
            raise


class ComplementoExames:
    """Seção "Atendimentos por médico" e planilha anexa do e-mail de atendimentos."""

    def __init__(
        self,
        processar: ProcessarExames,
        sgg: ExamesGateway,
        uow: FabricaUoW,
        planilha: GeradorPlanilha,
    ) -> None:
        self._processar = processar
        self._sgg = sgg
        self._uow = uow
        self._planilha = planilha

    def resumo_gravado(self, dia: date) -> Mapping[str, Any]:
        """Só o que já está no banco, sem consultar o SGG (prévia do e-mail no admin)."""
        with self._uow() as uow:
            medicos = uow.medicos.selecionados()
            coleta = uow.exames.coleta(dia)
            exames = uow.exames.do_dia(dia)
        if not medicos or coleta is None:
            return {}
        return {"exames_medicos": resumo_por_medico(exames, medicos, dia)}

    def para(self, dia: date, *, com_anexo: bool = True) -> Complemento:
        """``com_anexo=False`` (ninguém autorizado a receber a planilha): só as contagens, sem
        ler nomes no SGG."""
        with self._uow() as uow:
            medicos = uow.medicos.selecionados()
            coleta = uow.exames.coleta(dia)
        if not medicos:
            return Complemento({})  # nenhum médico escolhido: o e-mail sai como antes
        if coleta is None or set(coleta.medicos) != {m.crm for m in medicos}:
            # Não processado à noite, ou a escolha de médicos mudou depois: processa agora.
            self._processar.executar(dia)
        with self._uow() as uow:
            exames = uow.exames.do_dia(dia)

        resumo = resumo_por_medico(exames, medicos, dia)
        if not exames:
            return Complemento({"exames_medicos": resumo})
        sem_anexo = {"exames_medicos": {**resumo, "anexo_restrito": True}}
        if not com_anexo:
            return Complemento(sem_anexo)

        try:
            nomes = {e.id: e.funcionario for e in self._sgg.exames_clinicos(dia)}
        except ErroIntegracao as erro:
            # Sem o SGG, a planilha sai com o código do funcionário no lugar do nome.
            log.warning("exames_nomes_indisponiveis", erro=str(erro))
            nomes = {}
            resumo["nomes_indisponiveis"] = True
        com_nome = [
            replace(e, funcionario=nomes.get(e.id) or f"Funcionário #{e.id_funcionario}")
            for e in exames
        ]
        conteudo = self._planilha.atendimentos_por_medico(
            dia, linhas_da_planilha(com_nome, medicos)
        )
        anexo = Anexo(f"atendimentos-por-medico-{dia:%Y-%m-%d}.xlsx", conteudo, TIPO_XLSX)
        resumo["anexo"] = anexo.nome
        return Complemento({"exames_medicos": resumo}, (anexo,), sem_anexo)
