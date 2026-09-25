"""Monitor em tempo real do gerente: o dia de hoje até agora.

Lê só o banco — os eventos que o coletor grava a cada ciclo de polling. O monitor
nunca chama o SGG, então abrir a tela (em quantos navegadores for) não consome a cota
de requisições da API: a atualização dos dados continua limitada ao ciclo do coletor.

Os indicadores são os do e-mail diário, calculados com a mesma função sobre
``[hoje 00:00, agora]``. A comparação é com o mesmo dia da semana anterior **até o
mesmo horário** (comparar a manhã de hoje com o dia inteiro da semana passada
distorceria todos os totais).

O resultado fica em cache por alguns segundos: vários monitores abertos (TV da
recepção, celular do gerente) dividem o mesmo cálculo.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from relatorio.application.configuracao import JanelaColeta, inicio_do_dia
from relatorio.application.metricas import ObterMetricasPeriodo
from relatorio.application.ports import Relogio
from relatorio.domain.ao_vivo import SituacaoAoVivo, calcular_ao_vivo
from relatorio.domain.comparativo import comparar
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.metricas import calcular_metricas
from relatorio.domain.resumo import ResumoDiario

VALIDADE_CACHE = timedelta(seconds=20)


@dataclass(frozen=True, slots=True)
class Monitor:
    resumo: ResumoDiario
    ao_vivo: SituacaoAoVivo
    instante_base: datetime


class ObterMonitor:
    def __init__(
        self,
        metricas: ObterMetricasPeriodo,
        relogio: Relogio,
        janela: JanelaColeta,
        validade: timedelta = VALIDADE_CACHE,
    ) -> None:
        self._metricas = metricas
        self._relogio = relogio
        self._janela = janela
        self._validade = validade
        self._cache: tuple[datetime, Monitor] | None = None
        self._trava = threading.Lock()

    def executar(self) -> Monitor:
        agora = self._relogio.agora()
        # A trava faz requisições simultâneas esperarem um único cálculo.
        with self._trava:
            if self._cache is not None:
                calculado_em, monitor = self._cache
                if timedelta(0) <= agora - calculado_em < self._validade:
                    return monitor
            monitor = self._calcular(agora)
            self._cache = (agora, monitor)
            return monitor

    def _calcular(self, agora: datetime) -> Monitor:
        local = agora.astimezone(FUSO_BRASILIA)
        hoje = local.date()
        inicio = inicio_do_dia(hoje)
        dados = self._metricas.carregar(inicio, agora)
        metricas = calcular_metricas(
            dados.agendamentos,
            dados.agendas,
            dados.configuracao.regras,
            inicio,
            agora,
            falhas_coleta=dados.falhas_coleta,
        )

        data_base = hoje - timedelta(days=7)
        instante_base = local - timedelta(days=7)  # mesmo horário de parede
        base = self._metricas.executar(inicio_do_dia(data_base), instante_base).metricas
        kpis_base = None if base.sem_movimento else base.kpis

        return Monitor(
            resumo=ResumoDiario(
                data_referencia=hoje,
                unidades=dados.unidades,
                gerado_em=agora,
                metricas=metricas,
                comparativo=comparar(metricas.kpis, kpis_base, data_base),
            ),
            ao_vivo=calcular_ao_vivo(
                dados.agendamentos,
                agora,
                abertura=self._janela.inicio,
                fechamento=self._janela.fim,
                guiches={i for i, agenda in dados.agendas.items() if agenda.guiche},
            ),
            instante_base=instante_base,
        )
