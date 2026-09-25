from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.application.montagem import montar_casos_de_uso
from tests.fabricas import hora
from tests.fakes import (
    BancoEmMemoria,
    EmailFake,
    RelogioFixo,
    RenderizadorFake,
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
        self.alerta_coleta = casos.alerta_coleta

    def alertas_enviados(self) -> list[str]:
        return [c.assunto for _, c in self.email.enviados if c.assunto.startswith("[Alerta]")]


@pytest.fixture
def sistema() -> Sistema:
    return Sistema()
