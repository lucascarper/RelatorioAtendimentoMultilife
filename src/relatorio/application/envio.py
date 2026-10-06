"""RF05/RF09/RF10 — envio do relatório diário por e-mail."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import structlog

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.exames import Complemento
from relatorio.application.modelos import Anexo, ConteudoEmail, StatusEnvio
from relatorio.application.ports import (
    DestinatarioRepository,
    EnviadorEmail,
    FabricaUoW,
    Relogio,
    RenderizadorEmail,
    ResumoRepository,
    UnidadeDeTrabalho,
)
from relatorio.domain.entidades import FUSO_BRASILIA

log = structlog.get_logger(__name__)


class Consolidador(Protocol):
    def executar(self, dia: date) -> Mapping[str, object]: ...


class ComplementoRelatorio(Protocol):
    """Parte opcional do e-mail (ex.: atendimentos por médico): métricas extras e anexos."""

    def para(self, dia: date, *, com_anexo: bool = True) -> Complemento: ...


@dataclass(frozen=True, slots=True)
class TipoRelatorio:
    """O que muda entre os relatórios (atendimentos, financeiro, SESMT) no ciclo de envio."""

    nome: str  # usado nos alertas: "Relatório", "Relatório financeiro"
    resumos: Callable[[UnidadeDeTrabalho], ResumoRepository]
    destinatarios: Callable[[UnidadeDeTrabalho], DestinatarioRepository]
    renderizar: Callable[[RenderizadorEmail, Mapping[str, Any]], ConteudoEmail]


ATENDIMENTOS = TipoRelatorio(
    nome="Relatório",
    resumos=lambda uow: uow.resumos,
    destinatarios=lambda uow: uow.destinatarios,
    renderizar=lambda r, metricas: r.relatorio(metricas),
)
FINANCEIRO = TipoRelatorio(
    nome="Relatório financeiro",
    resumos=lambda uow: uow.resumos_financeiros,
    destinatarios=lambda uow: uow.destinatarios_financeiro,
    renderizar=lambda r, metricas: r.relatorio_financeiro(metricas),
)
SESMT = TipoRelatorio(
    nome="Relatório do SESMT",
    resumos=lambda uow: uow.resumos_sesmt,
    destinatarios=lambda uow: uow.destinatarios_sesmt,
    renderizar=lambda r, metricas: r.relatorio_sesmt(metricas),
)


PREFIXO_ENVIO_PARCIAL = "envio_parcial"


class RelatorioIndisponivel(Exception):
    """Não há resumo para a data, nem foi possível consolidá-lo."""


def dia_do_relatorio(agora: datetime) -> date:
    """O e-mail das 07:59 traz o dia anterior (na segunda-feira, o domingo)."""
    return agora.astimezone(FUSO_BRASILIA).date() - timedelta(days=1)


class EnviarRelatorio:
    def __init__(
        self,
        uow: FabricaUoW,
        relogio: Relogio,
        consolidar: Consolidador,
        renderizador: RenderizadorEmail,
        email: EnviadorEmail,
        alertar: AlertarTecnico,
        destinatarios_override: Sequence[str] = (),
        tipo: TipoRelatorio = ATENDIMENTOS,
        complemento: ComplementoRelatorio | None = None,
    ) -> None:
        self._uow = uow
        self._relogio = relogio
        self._consolidar = consolidar
        self._renderizador = renderizador
        self._email = email
        self._alertar = alertar
        self._override = tuple(destinatarios_override)
        self._tipo = tipo
        self._complemento = complemento

    def _complementar(self, dia: date, com_anexo: bool) -> Complemento:
        """O complemento nunca impede o relatório principal: se falhar, sai sem ele."""
        if self._complemento is None:
            return Complemento({})
        try:
            return self._complemento.para(dia, com_anexo=com_anexo)
        except Exception as erro:
            log.warning("complemento_falhou", dia=dia.isoformat(), erro=type(erro).__name__)
            self._alertar.enviar(
                f"{self._tipo.nome} enviado sem os atendimentos por médico",
                "Não foi possível montar a seção de atendimentos por médico e a planilha "
                "anexa. O relatório principal foi enviado normalmente.",
                {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
            )
            return Complemento({})

    def executar(self, dia: date, *, forcar: bool = False) -> dict[str, object]:
        referencia = dia.isoformat()
        with self._uow() as uow:
            registro = self._tipo.resumos(uow).obter(dia)
        if registro is None:
            # O envio só lê o resumo pronto; se ele não existe, tenta consolidar agora.
            try:
                self._consolidar.executar(dia)
            except Exception as erro:
                self._alertar.enviar(
                    f"{self._tipo.nome} não enviado",
                    f"Não havia resumo de {dia:%d/%m/%Y} e a consolidação de última hora "
                    "falhou. Nenhum relatório incompleto foi enviado.",
                    {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
                raise RelatorioIndisponivel(referencia) from erro
            with self._uow() as uow:
                registro = self._tipo.resumos(uow).obter(dia)
            if registro is None:
                raise RelatorioIndisponivel(referencia)

        with self._uow() as uow:
            if not self._tipo.resumos(uow).reservar_envio(dia, forcar=forcar):
                return {"referencia": referencia, "status": "ja_enviado"}
            ativos = self._tipo.destinatarios(uow).listar(apenas_ativos=True)
            uow.commit()
        if self._override:
            # Homologação: a caixa de teste recebe tudo, inclusive a planilha.
            destinatarios = list(self._override)
            com_anexo = set(destinatarios)
        else:
            destinatarios = [d.email for d in ativos]
            com_anexo = {d.email for d in ativos if d.recebe_anexo}

        if not destinatarios:
            with self._uow() as uow:
                self._tipo.resumos(uow).marcar_falha_envio(dia)
                uow.commit()
            self._alertar.enviar(
                f"{self._tipo.nome} sem destinatários",
                "Não há destinatários ativos cadastrados no admin para este relatório.",
                {"Data": f"{dia:%d/%m/%Y}"},
            )
            return {"referencia": referencia, "status": "sem_destinatarios"}

        complemento = self._complementar(dia, com_anexo=bool(com_anexo))
        self._enviar_grupos(dia, forcar, registro.metricas, destinatarios, com_anexo, complemento)

        with self._uow() as uow:
            self._tipo.resumos(uow).marcar_enviado(dia, self._relogio.agora())
            uow.configuracoes.remover(self._chave_parcial(dia))
            uow.commit()
        resultado: dict[str, object] = {
            "referencia": referencia,
            "status": "enviado",
            "destinatarios": len(destinatarios),
            "sem_movimento": bool(registro.metricas.get("sem_movimento")),
        }
        if complemento.metricas:
            # Também registra quando a planilha não foi para ninguém (ninguém autorizado).
            resultado["anexos"] = len(complemento.anexos)
            resultado["com_anexo"] = (
                sum(1 for d in destinatarios if d in com_anexo) if complemento.anexos else 0
            )
        return resultado

    def _chave_parcial(self, dia: date) -> str:
        return f"{PREFIXO_ENVIO_PARCIAL}:{self._tipo.nome}:{dia.isoformat()}"

    def _enviar_grupos(
        self,
        dia: date,
        forcar: bool,
        metricas: Mapping[str, Any],
        destinatarios: Sequence[str],
        com_anexo: set[str],
        complemento: Complemento,
    ) -> None:
        """Envia cada grupo (sem e com planilha); marca falha e relança se algum falhar."""
        # Com e sem planilha são dois e-mails: quem já recebeu numa tentativa anterior do dia
        # não recebe de novo (nem a planilha nominal), a não ser num reenvio forçado.
        chave = self._chave_parcial(dia)
        with self._uow() as uow:
            if forcar:
                uow.configuracoes.remover(chave)
                uow.commit()
                ja_receberam: set[str] = set()
            else:
                texto = uow.configuracoes.obter_todas().get(chave, "")
                ja_receberam = {e for e in texto.split(",") if e}
        falha: Exception | None = None
        for grupo, extra, anexos in self._grupos(destinatarios, com_anexo, complemento):
            pendentes = [d for d in grupo if d not in ja_receberam]
            if not pendentes:
                continue
            try:
                conteudo = self._tipo.renderizar(self._renderizador, {**metricas, **extra})
                if anexos:
                    conteudo = replace(conteudo, anexos=anexos)
                self._email.enviar(pendentes, conteudo)
            except Exception as erro:  # um grupo que falha não impede o outro
                falha = falha or erro
                continue
            ja_receberam.update(pendentes)
            with self._uow() as uow:
                uow.configuracoes.definir(chave, ",".join(sorted(ja_receberam)))
                uow.commit()
        if falha is not None:
            with self._uow() as uow:
                self._tipo.resumos(uow).marcar_falha_envio(dia)
                uow.commit()
            raise falha

    @staticmethod
    def _grupos(
        destinatarios: Sequence[str], com_anexo: set[str], complemento: Complemento
    ) -> list[tuple[list[str], Mapping[str, Any], tuple[Anexo, ...]]]:
        """Um e-mail sem a planilha para os demais e outro, com ela, para os autorizados.

        O sem planilha vai primeiro: um problema só no anexo (ex.: limite de tamanho do
        servidor) não deixa os demais sem o relatório.
        """
        if not complemento.anexos:
            return [(list(destinatarios), complemento.metricas, ())]
        autorizados = [d for d in destinatarios if d in com_anexo]
        demais = [d for d in destinatarios if d not in com_anexo]
        grupos: list[tuple[list[str], Mapping[str, Any], tuple[Anexo, ...]]] = []
        if demais:
            grupos.append((demais, complemento.para_quem_nao_recebe_anexo(), ()))
        if autorizados:
            grupos.append((autorizados, complemento.metricas, complemento.anexos))
        return grupos

    def executar_agendado(self, dia: date, tentativa: int, total: int = 3) -> dict[str, object]:
        """07:59, 08:01 e 08:03. Na última falha, alerta o técnico."""
        try:
            return self.executar(dia)
        except RelatorioIndisponivel:
            raise  # o alerta já foi enviado
        except Exception as erro:
            if tentativa >= total:
                self._alertar.enviar(
                    f"Falha no envio do {self._tipo.nome.lower()}",
                    f"O e-mail de {dia:%d/%m/%Y} não pôde ser enviado após {total} tentativas.",
                    {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
            raise


class VerificarEnvio:
    """08:10 — rede de segurança: o relatório de ontem foi entregue?"""

    def __init__(
        self, uow: FabricaUoW, alertar: AlertarTecnico, tipo: TipoRelatorio = ATENDIMENTOS
    ) -> None:
        self._uow = uow
        self._alertar = alertar
        self._tipo = tipo

    def executar(self, dia: date) -> dict[str, object]:
        with self._uow() as uow:
            registro = self._tipo.resumos(uow).obter(dia)
        status = registro.status_envio if registro else None
        if status is StatusEnvio.ENVIADO:
            return {"referencia": dia.isoformat(), "status": "ok"}
        if status is StatusEnvio.FALHA:
            # A terceira tentativa já avisou o técnico; não duplica o alerta.
            return {"referencia": dia.isoformat(), "status": "falha_ja_alertada"}
        self._alertar.enviar(
            f"{self._tipo.nome} não entregue até 08:10",
            f"{self._tipo.nome} de {dia:%d/%m/%Y} ainda não consta como enviado.",
            {"Data": f"{dia:%d/%m/%Y}", "Situação do envio": status or "sem resumo"},
        )
        return {"referencia": dia.isoformat(), "status": "alertado"}
