"""RF05/RF09/RF10 — envio do relatório diário por e-mail."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from relatorio.application.alertas import AlertarTecnico
from relatorio.application.consolidacao import ConsolidarDia
from relatorio.application.modelos import StatusEnvio
from relatorio.application.ports import EnviadorEmail, FabricaUoW, Relogio, RenderizadorEmail
from relatorio.domain.entidades import FUSO_BRASILIA


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
        consolidar: ConsolidarDia,
        renderizador: RenderizadorEmail,
        email: EnviadorEmail,
        alertar: AlertarTecnico,
        destinatarios_override: Sequence[str] = (),
    ) -> None:
        self._uow = uow
        self._relogio = relogio
        self._consolidar = consolidar
        self._renderizador = renderizador
        self._email = email
        self._alertar = alertar
        self._override = tuple(destinatarios_override)

    def executar(self, dia: date, *, forcar: bool = False) -> dict[str, object]:
        referencia = dia.isoformat()
        with self._uow() as uow:
            registro = uow.resumos.obter(dia)
        if registro is None:
            # O envio só lê o resumo pronto; se ele não existe, tenta consolidar agora.
            try:
                self._consolidar.executar(dia)
            except Exception as erro:
                self._alertar.enviar(
                    "Relatório não enviado",
                    f"Não havia resumo de {dia:%d/%m/%Y} e a consolidação de última hora "
                    "falhou. Nenhum relatório incompleto foi enviado.",
                    {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
                raise RelatorioIndisponivel(referencia) from erro
            with self._uow() as uow:
                registro = uow.resumos.obter(dia)
            if registro is None:
                raise RelatorioIndisponivel(referencia)

        with self._uow() as uow:
            if not uow.resumos.reservar_envio(dia, forcar=forcar):
                return {"referencia": referencia, "status": "ja_enviado"}
            destinatarios = list(self._override) or [
                d.email for d in uow.destinatarios.listar(apenas_ativos=True)
            ]
            uow.commit()

        if not destinatarios:
            with self._uow() as uow:
                uow.resumos.marcar_falha_envio(dia)
                uow.commit()
            self._alertar.enviar(
                "Relatório sem destinatários",
                "Não há destinatários ativos cadastrados no admin.",
                {"Data": f"{dia:%d/%m/%Y}"},
            )
            return {"referencia": referencia, "status": "sem_destinatarios"}

        try:
            conteudo = self._renderizador.relatorio(registro.metricas)
            self._email.enviar(destinatarios, conteudo)
        except Exception:
            with self._uow() as uow:
                uow.resumos.marcar_falha_envio(dia)
                uow.commit()
            raise

        with self._uow() as uow:
            uow.resumos.marcar_enviado(dia, self._relogio.agora())
            uow.commit()
        return {
            "referencia": referencia,
            "status": "enviado",
            "destinatarios": len(destinatarios),
            "sem_movimento": bool(registro.metricas.get("sem_movimento")),
        }

    def executar_agendado(self, dia: date, tentativa: int, total: int = 3) -> dict[str, object]:
        """07:59, 08:01 e 08:03. Na última falha, alerta o técnico."""
        try:
            return self.executar(dia)
        except RelatorioIndisponivel:
            raise  # o alerta já foi enviado
        except Exception as erro:
            if tentativa >= total:
                self._alertar.enviar(
                    "Falha no envio do relatório",
                    f"O e-mail de {dia:%d/%m/%Y} não pôde ser enviado após {total} tentativas.",
                    {"Data": f"{dia:%d/%m/%Y}", "Erro": f"{type(erro).__name__}: {erro}"},
                )
            raise


class VerificarEnvio:
    """08:10 — rede de segurança: o relatório de ontem foi entregue?"""

    def __init__(self, uow: FabricaUoW, alertar: AlertarTecnico) -> None:
        self._uow = uow
        self._alertar = alertar

    def executar(self, dia: date) -> dict[str, object]:
        with self._uow() as uow:
            registro = uow.resumos.obter(dia)
        status = registro.status_envio if registro else None
        if status is StatusEnvio.ENVIADO:
            return {"referencia": dia.isoformat(), "status": "ok"}
        if status is StatusEnvio.FALHA:
            # A terceira tentativa já avisou o técnico; não duplica o alerta.
            return {"referencia": dia.isoformat(), "status": "falha_ja_alertada"}
        self._alertar.enviar(
            "Relatório não entregue até 08:10",
            f"O relatório de {dia:%d/%m/%Y} ainda não consta como enviado.",
            {"Data": f"{dia:%d/%m/%Y}", "Situação do envio": status or "sem resumo"},
        )
        return {"referencia": dia.isoformat(), "status": "alertado"}
