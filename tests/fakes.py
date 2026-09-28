"""Dublês de teste: relógio, SGG, e-mail e renderizador falsos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from relatorio.application.modelos import ConteudoEmail
from relatorio.application.ports import ErroIntegracao
from relatorio.domain.entidades import Agenda, AgendamentoSgg, Situacao
from relatorio.domain.financeiro import Contrato, PrecoFornecedor, Titulo
from relatorio.infrastructure.memoria import BancoEmMemoria, UoWEmMemoria

__all__ = [
    "BancoEmMemoria",
    "EmailFake",
    "RelogioFixo",
    "RenderizadorFake",
    "SggFake",
    "UoWEmMemoria",
]


class RelogioFixo:
    def __init__(self, instante: datetime) -> None:
        self.instante = instante

    def agora(self) -> datetime:
        return self.instante

    def avancar(self, **kwargs: float) -> None:
        self.instante += timedelta(**kwargs)


class ErroSggFake(ErroIntegracao):
    pass


@dataclass
class SggFake:
    """Responde com listas pré-programadas e registra as chamadas."""

    editados: list[list[AgendamentoSgg]] = field(default_factory=list)
    do_dia: list[AgendamentoSgg] = field(default_factory=list)
    lista_agendas: list[Agenda] = field(default_factory=list)
    falhar: bool = False
    chamadas: list[tuple[str, object, object]] = field(default_factory=list)
    requisicoes_realizadas: int = 0
    ignorados_na_ultima_consulta: dict[int, str] = field(default_factory=dict)

    def _registrar(self, nome: str, a: object, b: object) -> None:
        self.chamadas.append((nome, a, b))
        self.requisicoes_realizadas += 1
        if self.falhar:
            raise ErroSggFake("SGG indisponível")

    def agendamentos_editados(self, de: datetime, ate: datetime) -> list[AgendamentoSgg]:
        self._registrar("editados", de, ate)
        return self.editados.pop(0) if self.editados else []

    def agendamentos_do_dia(
        self, dia: date, situacao: Situacao | None = None
    ) -> list[AgendamentoSgg]:
        self._registrar("do_dia", dia, situacao)
        return [r for r in self.do_dia if situacao is None or r.situacao is situacao]

    def agendas(self) -> list[Agenda]:
        self._registrar("agendas", None, None)
        return list(self.lista_agendas)


@dataclass
class FinanceiroFake:
    """Endpoints financeiros do SGG com listas pré-programadas."""

    recebidos: list[Titulo] = field(default_factory=list)
    pagos: list[Titulo] = field(default_factory=list)
    emitidos: list[Titulo] = field(default_factory=list)
    vencidos: list[Titulo] = field(default_factory=list)
    a_receber: list[Titulo] = field(default_factory=list)
    a_pagar: list[Titulo] = field(default_factory=list)
    contratos: list[Contrato] = field(default_factory=list)
    precos: dict[int, list[PrecoFornecedor]] = field(default_factory=dict)
    falhar: bool = False
    chamadas: list[str] = field(default_factory=list)

    def _chamar(self, nome: str) -> None:
        self.chamadas.append(nome)
        if self.falhar:
            raise ErroSggFake("SGG indisponível")

    def receber_pagos(self, de: date, ate: date) -> list[Titulo]:
        self._chamar("receber_pagos")
        return [t for t in self.recebidos if t.pagamento and de <= t.pagamento <= ate]

    def pagar_pagos(self, de: date, ate: date) -> list[Titulo]:
        self._chamar("pagar_pagos")
        return [t for t in self.pagos if t.pagamento and de <= t.pagamento <= ate]

    def receber_emitidos(self, de: date, ate: date) -> list[Titulo]:
        self._chamar("receber_emitidos")
        return [t for t in self.emitidos if t.emissao and de <= t.emissao <= ate]

    def receber_vencidos(self) -> list[Titulo]:
        self._chamar("receber_vencidos")
        return list(self.vencidos)

    def receber_a_vencer(self, de: date, ate: date) -> list[Titulo]:
        self._chamar("receber_a_vencer")
        return list(self.a_receber)

    def pagar_a_vencer(self, de: date, ate: date) -> list[Titulo]:
        self._chamar("pagar_a_vencer")
        return list(self.a_pagar)

    def contratos_ativos(self) -> list[Contrato]:
        self._chamar("contratos_ativos")
        return list(self.contratos)

    def precos_servico(self, id_servico: int) -> list[PrecoFornecedor]:
        self._chamar(f"precos_{id_servico}")
        return list(self.precos.get(id_servico, []))


@dataclass
class EmailFake:
    enviados: list[tuple[list[str], ConteudoEmail]] = field(default_factory=list)
    falhar: bool = False

    def enviar(self, destinatarios: Sequence[str], conteudo: ConteudoEmail) -> None:
        if self.falhar:
            raise ConnectionError("SMTP fora do ar")
        self.enviados.append((list(destinatarios), conteudo))


class RenderizadorFake:
    def relatorio(self, metricas: Mapping[str, Any]) -> ConteudoEmail:
        return ConteudoEmail(
            assunto=str(metricas.get("assunto", "")),
            html=f"<p>{metricas.get('kpis', {}).get('atendimentos')}</p>",
            texto="texto",
        )

    def relatorio_financeiro(self, metricas: Mapping[str, Any]) -> ConteudoEmail:
        return ConteudoEmail(
            assunto=f"Financeiro {metricas.get('referencia')}", html="<p>fin</p>", texto="fin"
        )

    def alerta(self, titulo: str, mensagem: str, detalhes: Mapping[str, str]) -> ConteudoEmail:
        return ConteudoEmail(assunto=f"[Alerta] {titulo}", html=mensagem, texto=mensagem)
