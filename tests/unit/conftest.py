from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.application.montagem import montar_casos_de_uso
from relatorio.infrastructure.planilha import GeradorPlanilhaXlsx
from tests.fabricas import hora
from tests.fakes import (
    BancoEmMemoria,
    EmailFake,
    ExamesFake,
    FinanceiroFake,
    RelogioFixo,
    RenderizadorFake,
    SesmtFake,
    SggFake,
    UoWEmMemoria,
)


@dataclass
class Sistema:
    """Casos de uso montados sobre fakes (mesma composição do container real)."""

    banco: BancoEmMemoria = field(default_factory=BancoEmMemoria)
    relogio: RelogioFixo = field(default_factory=lambda: RelogioFixo(hora("08:00")))
    sgg: SggFake = field(default_factory=SggFake)
    email: EmailFake = field(default_factory=EmailFake)
    financeiro: FinanceiroFake = field(default_factory=FinanceiroFake)
    sesmt: SesmtFake = field(default_factory=SesmtFake)
    exames: ExamesFake = field(default_factory=ExamesFake)
    coletor_habilitado: bool = True

    def __post_init__(self) -> None:
        self.uow = lambda: UoWEmMemoria(self.banco)
        casos = montar_casos_de_uso(
            uow=self.uow,
            relogio=self.relogio,
            sgg=self.sgg,
            email=self.email,
            renderizador=RenderizadorFake(),
            configuracao_padrao=ConfiguracaoRelatorio(),
            janela=JanelaColeta(),
            coletor_habilitado=self.coletor_habilitado,
            financeiro=self.financeiro,
            sesmt=self.sesmt,
            exames=self.exames,
            planilha=GeradorPlanilhaXlsx(),
        )
        self.alertar = casos.alertar
        self.coletar = casos.coletar
        self.reconciliar = casos.reconciliar
        self.sincronizar = casos.sincronizar
        self.metricas = casos.metricas
        self.monitor = casos.monitor
        self.consolidar = casos.consolidar
        self.enviar = casos.enviar
        self.verificar_envio = casos.verificar_envio
        self.reprocessar = casos.reprocessar
        self.limpar = casos.limpar
        self.compactar = casos.compactar
        self.alerta_coleta = casos.alerta_coleta
        assert casos.consolidar_financeiro is not None and casos.enviar_financeiro is not None
        self.consolidar_financeiro = casos.consolidar_financeiro
        self.enviar_financeiro = casos.enviar_financeiro
        self.verificar_financeiro = casos.verificar_financeiro
        assert casos.consolidar_sesmt is not None and casos.enviar_sesmt is not None
        self.consolidar_sesmt = casos.consolidar_sesmt
        self.enviar_sesmt = casos.enviar_sesmt
        self.verificar_sesmt = casos.verificar_sesmt
        assert casos.processar_exames is not None and casos.complemento_exames is not None
        self.processar_exames = casos.processar_exames
        self.complemento_exames = casos.complemento_exames

    def alertas_enviados(self) -> list[str]:
        return [c.assunto for _, c in self.email.enviados if c.assunto.startswith("[Alerta]")]


@pytest.fixture
def sistema() -> Sistema:
    return Sistema()
