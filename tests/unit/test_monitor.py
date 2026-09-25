"""Monitor em tempo real: caso de uso (cache, comparação, sem SGG) e apresentação."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from relatorio.application.metricas import JOB_COLETA
from relatorio.application.modelos import ExecucaoJob, StatusJob
from relatorio.domain.entidades import Situacao
from relatorio.interfaces.web.dependencias import SituacaoColeta
from relatorio.interfaces.web.monitor import (
    PainelMonitor,
    caminho_coluna,
    escala,
    montar_painel,
)
from tests.fabricas import DIA, evento, hora, snapshot
from tests.unit.conftest import Sistema

SEMANA_PASSADA = DIA - timedelta(days=7)


def registrar(
    sistema: Sistema, id_agendamento: int, dia: date, *fluxo: tuple[Situacao, str]
) -> None:
    """Grava snapshot + eventos de um agendamento (como o coletor faria)."""
    with sistema.uow() as uow:
        anterior: Situacao | None = None
        for situacao, quando in fluxo:
            uow.eventos.inserir(evento(id_agendamento, anterior, situacao, quando, dia))
            anterior = situacao
        uow.snapshots.salvar(
            snapshot(id_agendamento, anterior or Situacao.AGENDADO, data_agendamento=dia)
        )
        uow.commit()


def atendido(sistema: Sistema, id_agendamento: int, dia: date, chegada: str, fim: str) -> None:
    chamada = (hora(chegada) + (hora(fim) - hora(chegada)) / 2).strftime("%H:%M")
    registrar(
        sistema,
        id_agendamento,
        dia,
        (Situacao.AGENDADO, "06:00"),
        (Situacao.AGUARDANDO, chegada),
        (Situacao.EM_ATENDIMENTO, chamada),
        (Situacao.ATENDIDO, fim),
    )


@pytest.fixture
def dia_movimentado(sistema: Sistema) -> Sistema:
    atendido(sistema, 1, DIA, "07:50", "08:20")
    registrar(sistema, 2, DIA, (Situacao.AGENDADO, "06:00"), (Situacao.AGUARDANDO, "09:30"))
    # Semana passada: 2 atendimentos até as 10:00 e 1 depois (não pode entrar na base).
    atendido(sistema, 11, SEMANA_PASSADA, "07:40", "08:10")
    atendido(sistema, 12, SEMANA_PASSADA, "08:30", "09:00")
    atendido(sistema, 13, SEMANA_PASSADA, "10:30", "11:00")
    sistema.relogio.instante = hora("10:00")
    return sistema


class TestObterMonitor:
    def test_indicadores_do_dia_ate_agora_sem_chamar_o_sgg(self, dia_movimentado: Sistema) -> None:
        m = dia_movimentado.monitor.executar()
        k = m.resumo.metricas.kpis
        assert (k.agendados, k.atendimentos) == (2, 1)
        assert m.resumo.metricas.fim == hora("10:00")
        assert (m.ao_vivo.aguardando, m.ao_vivo.espera_atual_maxima_s) == (1, 30 * 60)
        assert dia_movimentado.sgg.chamadas == []

    def test_compara_com_a_semana_anterior_ate_o_mesmo_horario(
        self, dia_movimentado: Sistema
    ) -> None:
        m = dia_movimentado.monitor.executar()
        assert m.instante_base == hora("10:00", SEMANA_PASSADA)
        item = m.resumo.comparativo.item("atendimentos")
        assert item is not None
        assert (item.atual, item.anterior) == (1, 2)  # o das 11:00 ficou fora
        assert item.variacao == pytest.approx(-0.5)

    def test_sem_movimento_na_semana_anterior_nao_compara(self, sistema: Sistema) -> None:
        atendido(sistema, 1, DIA, "07:50", "08:20")
        sistema.relogio.instante = hora("10:00")
        assert sistema.monitor.executar().resumo.comparativo.disponivel is False

    def test_cache_divide_o_calculo_entre_monitores_abertos(self, dia_movimentado: Sistema) -> None:
        primeiro = dia_movimentado.monitor.executar()
        dia_movimentado.relogio.avancar(seconds=15)
        assert dia_movimentado.monitor.executar() is primeiro
        dia_movimentado.relogio.avancar(seconds=10)
        novo = dia_movimentado.monitor.executar()
        assert novo is not primeiro
        assert novo.ao_vivo.espera_atual_maxima_s == 30 * 60 + 25


class TestPainel:
    def painel(self, sistema: Sistema, coleta: SituacaoColeta | None = None) -> PainelMonitor:
        m = sistema.monitor.executar()
        ultima = ExecucaoJob(
            id=1,
            job=JOB_COLETA,
            inicio=hora("09:59:30"),
            fim=hora("09:59:31"),
            status=StatusJob.SUCESSO,
            detalhe={},
        )
        coleta = coleta or SituacaoColeta(
            ultima=ultima, em_coleta=True, atrasada=False, agora=hora("10:00")
        )
        return montar_painel(m, coleta, coletor_habilitado=True)

    def test_narrativa_cartoes_e_comparacao(self, dia_movimentado: Sistema) -> None:
        p = self.painel(dia_movimentado)
        assert p.narrativa.startswith("1 pessoa na recepção agora; a maior espera é de 30 min.")
        assert "Hoje até agora: 1 atendimentos" in p.narrativa
        assert [c.valor for c in p.ao_vivo] == ["1", "0", "0"]
        assert p.comparacao == "comparado com qua 16/09/2026 até 10:00"
        atendimentos = p.kpis[0]
        assert atendimentos.delta is not None
        assert atendimentos.delta.rotulo == "vs qua 16/09 até 10:00"
        assert (p.dados_de, p.coleta_estado) == ("09:59:30", "ok")

    def test_fila_do_dia_nao_vira_alerta(self, dia_movimentado: Sistema) -> None:
        titulos = [a.titulo for a in self.painel(dia_movimentado).a.alertas]
        assert not any("sem baixa" in t or "não finalizados" in t for t in titulos)

    def test_grafico_por_hora(self, dia_movimentado: Sistema) -> None:
        g = self.painel(dia_movimentado).grafico
        assert [h.rotulo for h in g.grupos][:2] == ["06h", "07h"]
        atual = next(h for h in g.grupos if h.atual)
        assert atual.rotulo == "10h"
        assert all(h.futuro for h in g.grupos if h.rotulo > "10h")
        sete = next(h for h in g.grupos if h.rotulo == "07h")
        assert [bool(c.caminho) for c in sete.colunas] == [True, False]  # chegou, não saiu
        assert sete.dica["linhas"][0][2] == "1"

    def test_coleta_atrasada_avisa(self, dia_movimentado: Sistema) -> None:
        coleta = SituacaoColeta(ultima=None, em_coleta=True, atrasada=True, agora=hora("10:00"))
        p = self.painel(dia_movimentado, coleta)
        assert p.coleta_estado == "erro"


@pytest.mark.parametrize(
    ("maximo", "topo", "marcas"),
    [
        (0, 1, (0, 1)),
        (3, 3, (0, 1, 2, 3)),
        (7, 8, (0, 2, 4, 6, 8)),
        (23, 30, (0, 10, 20, 30)),
        (160, 200, (0, 50, 100, 150, 200)),
    ],
)
def test_escala_do_eixo(maximo: int, topo: int, marcas: tuple[int, ...]) -> None:
    assert escala(maximo) == (topo, marcas)


def test_coluna_vazia_e_ponta_arredondada() -> None:
    assert caminho_coluna(10, 20, 100, 100) == ""
    caminho = caminho_coluna(10, 20, 50, 100)
    assert caminho.startswith("M10.0,100.0V54.0Q10.0,50.0 14.0,50.0")
    assert caminho.endswith("V100.0Z")
