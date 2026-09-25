"""Turnos, entidades, comparativo, disponibilidade da coleta, filtro e resumo."""

from __future__ import annotations

import json
from datetime import date, time, timedelta

import pytest

from relatorio.domain.comparativo import comparar
from relatorio.domain.disponibilidade import janelas_sem_coleta
from relatorio.domain.entidades import Situacao, Turno
from relatorio.domain.filtro import FiltroRelatorio
from relatorio.domain.metricas import FalhaColeta, Kpis, RegrasMetricas, calcular_metricas
from relatorio.domain.resumo import ResumoDiario, assunto_email, dia_semana, para_json
from relatorio.domain.turnos import ConfiguracaoTurnos
from tests.fabricas import DIA, agenda, atendimento, com_situacao, hora


class TestTurnos:
    @pytest.mark.parametrize(
        ("horario", "turno"),
        [
            (time(5, 30), Turno.MANHA),
            (time(6, 0), Turno.MANHA),
            (time(12, 59), Turno.MANHA),
            (time(13, 0), Turno.TARDE),
            (time(18, 0), Turno.TARDE),
            (time(19, 0), Turno.TARDE),
        ],
    )
    def test_classificacao(self, horario: time, turno: Turno) -> None:
        assert ConfiguracaoTurnos().classificar(horario) is turno

    def test_corte_configuravel(self) -> None:
        turnos = ConfiguracaoTurnos(tarde_inicio=time(12, 0))
        assert turnos.classificar(time(12, 0)) is Turno.TARDE
        assert turnos.faixa(Turno.MANHA) == "06:00 às 11:59"

    def test_configuracao_invalida(self) -> None:
        with pytest.raises(ValueError, match="Turnos inválidos"):
            ConfiguracaoTurnos(manha_inicio=time(14, 0))

    def test_rotulos(self) -> None:
        assert Turno.MANHA.rotulo == "Manhã"
        assert Turno.TARDE.rotulo == "Tarde"


class TestEntidades:
    @pytest.mark.parametrize(
        "texto", ["Em Atendimento", "em atendimento", "EM  ATENDIMENTO", " Em Atendimento "]
    )
    def test_situacao_de_texto_tolerante(self, texto: str) -> None:
        assert Situacao.de_texto(texto) is Situacao.EM_ATENDIMENTO

    def test_situacao_desconhecida(self) -> None:
        with pytest.raises(ValueError, match="desconhecida"):
            Situacao.de_texto("Remarcado")

    def test_situacoes_finais(self) -> None:
        assert Situacao.ATENDIDO.final
        assert not Situacao.AGUARDANDO.final

    def test_consultorio_usa_sala_com_fallback_para_nome(self) -> None:
        assert agenda(1, "Clínico", sala="Sala 03").consultorio == "Sala 03"
        assert agenda(1, "Clínico", sala="  ").consultorio == "Clínico"
        assert agenda(1, "Clínico").consultorio == "Clínico"


def kpis(**valores: object) -> Kpis:
    base: dict[str, object] = {
        "agendados": 100,
        "atendimentos": 80,
        "faltas": 10,
        "taxa_faltas": 0.10,
        "espera_media_s": 600,
        "tma_s": 900,
    }
    base.update(valores)
    return Kpis.de_dict(base)


class TestComparativo:
    def test_variacoes_e_avaliacao(self) -> None:
        c = comparar(
            kpis(atendimentos=88, faltas=12, taxa_faltas=0.12, espera_media_s=540, tma_s=990),
            kpis(),
            date(2026, 9, 16),
        )
        assert c.disponivel
        atend = c.item("atendimentos")
        assert atend is not None
        assert atend.variacao == pytest.approx(0.10)
        assert (atend.direcao, atend.avaliacao) == ("alta", "positiva")
        faltas = c.item("faltas")
        assert faltas is not None
        assert (faltas.direcao, faltas.avaliacao) == ("alta", "negativa")
        taxa = c.item("taxa_faltas")
        assert taxa is not None
        assert taxa.em_pontos
        assert taxa.variacao == pytest.approx(0.02)
        espera = c.item("espera_media_s")
        assert espera is not None
        assert (espera.direcao, espera.avaliacao) == ("baixa", "positiva")
        tma = c.item("tma_s")
        assert tma is not None
        assert (tma.direcao, tma.avaliacao) == ("alta", "neutra")

    def test_sem_base(self) -> None:
        c = comparar(kpis(), None, date(2026, 9, 16))
        assert not c.disponivel
        assert all(i.variacao is None and i.direcao is None for i in c.itens)
        assert c.item("inexistente") is None

    def test_base_zero_nao_divide(self) -> None:
        c = comparar(kpis(faltas=3), kpis(faltas=0), date(2026, 9, 16))
        item = c.item("faltas")
        assert item is not None
        assert item.variacao is None

    def test_variacao_pequena_e_estavel(self) -> None:
        c = comparar(kpis(atendimentos=1000), kpis(atendimentos=1003), date(2026, 9, 16))
        item = c.item("atendimentos")
        assert item is not None
        assert (item.direcao, item.avaliacao) == ("estavel", "neutra")


class TestDisponibilidadeColeta:
    def test_sem_falhas(self) -> None:
        sucessos = [hora(f"{h:02d}:{m:02d}") for h in range(6, 18) for m in range(60)]
        sucessos.append(hora("18:00"))
        assert janelas_sem_coleta(sucessos, hora("06:00"), hora("18:00")) == ()

    def test_lacuna_no_meio_do_dia(self) -> None:
        sucessos = [hora("06:00"), hora("10:11"), hora("10:40"), hora("18:00")]
        falhas = janelas_sem_coleta(sucessos, hora("06:00"), hora("18:00"))
        assert [(f.inicio, f.fim, f.minutos) for f in falhas] == [
            (hora("06:01"), hora("10:11"), 250),
            (hora("10:12"), hora("10:40"), 28),
            (hora("10:41"), hora("18:00"), 439),
        ]

    def test_worker_parado_desde_a_abertura_conta_do_inicio_da_janela(self) -> None:
        falhas = janelas_sem_coleta([hora("07:00")], hora("06:00"), hora("07:01"))
        assert [(f.inicio, f.fim) for f in falhas] == [(hora("06:00"), hora("07:00"))]

    def test_worker_parado_o_dia_todo(self) -> None:
        falhas = janelas_sem_coleta([], hora("06:00"), hora("18:00"))
        assert len(falhas) == 1
        assert falhas[0].minutos == 720

    def test_janela_vazia(self) -> None:
        assert janelas_sem_coleta([], hora("18:00"), hora("06:00")) == ()


AGENDAS_FILTRO = {
    10: agenda(10, "Clínico", unidade=1),
    20: agenda(20, "Audiometria", unidade=2),
    30: agenda(30, "Raio X", unidade=1, incluir=False),
}


class TestFiltro:
    def test_todas_as_unidades_respeitando_agendas_excluidas(self) -> None:
        ags = [
            atendimento(1, id_agenda=10),
            atendimento(2, id_agenda=20, unidade=2),
            atendimento(3, id_agenda=30),
        ]
        assert [a.id_agendamento for a in FiltroRelatorio().aplicar(ags, AGENDAS_FILTRO)] == [1, 2]

    def test_unidades_selecionadas(self) -> None:
        ags = [
            atendimento(1, id_agenda=10),
            atendimento(2, id_agenda=20, unidade=2),
            atendimento(3, id_agenda=None, unidade=None),
            atendimento(4, id_agenda=20, unidade=None),
        ]
        filtro = FiltroRelatorio(unidades=frozenset({2}))
        assert [a.id_agendamento for a in filtro.aplicar(ags, AGENDAS_FILTRO)] == [2, 4]


class TestResumo:
    def test_dia_semana_e_assunto(self) -> None:
        assert dia_semana(DIA) == "quarta-feira"
        assert assunto_email(DIA) == "Resumo de Atendimentos — 23/09/2026 (quarta-feira)"

    def test_json_serializavel_e_completo(self) -> None:
        metricas = calcular_metricas(
            [atendimento(1), com_situacao(2, Situacao.FALTOU)],
            {10: agenda(10, "Clínico", sala="Sala 01")},
            RegrasMetricas(),
            hora("00:00"),
            hora("23:59:59"),
        )
        resumo = ResumoDiario(
            data_referencia=DIA,
            unidades=("Unidade Centro",),
            gerado_em=hora("23:00"),
            metricas=metricas,
            comparativo=comparar(metricas.kpis, None, date(2026, 9, 16)),
        )
        dados = resumo.para_json()
        json.dumps(dados)  # não pode lançar
        assert dados["versao_regra"] == "1.2.0"
        assert dados["dia_semana"] == "quarta-feira"
        assert dados["sem_movimento"] is False
        assert dados["kpis"]["atendimentos"] == 1
        assert dados["por_turno"]["manha"]["faixa"] == "06:00 às 12:59"
        assert dados["consultorios"][0]["consultorio"] == "Sala 01"
        assert dados["comparativo"]["disponivel"] is False
        assert dados["alertas"]["quantidade"] == 0
        assert dados["limites_atipico"] == {"min_minutos": 1, "max_minutos": 180}

    def test_para_json_tipos(self) -> None:
        assert para_json(time(8, 5)) == "08:05"
        assert para_json(timedelta(minutes=2)) == 120
        assert para_json(FalhaColeta(hora("10:12"), hora("10:40")))["minutos"] == 28
        assert para_json({1: {Situacao.FALTOU}}) == {"1": ["Faltou"]}
