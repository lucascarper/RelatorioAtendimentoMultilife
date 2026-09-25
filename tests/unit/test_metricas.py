"""Cálculo das métricas — casos obrigatórios da seção 15 e regras da seção 4."""

from __future__ import annotations

from dataclasses import replace
from datetime import time, timedelta

import pytest

from relatorio.domain.entidades import Situacao, Turno
from relatorio.domain.metricas import (
    AgendamentoDoDia,
    FalhaColeta,
    Kpis,
    RegrasMetricas,
    calcular_metricas,
    classificar_atipico,
    definir_turno,
    medir_tempos,
    situacao_em,
)
from relatorio.domain.turnos import ConfiguracaoTurnos
from tests.fabricas import DIA, agenda, atendimento, com_situacao, evento, hora

INICIO, FIM = hora("00:00"), hora("23:59:59")
REGRAS = RegrasMetricas()
AGENDAS = {
    10: agenda(10, "Clínico", sala="Sala 01"),
    20: agenda(20, "Audiometria"),
}


def calcular(agendamentos: list[AgendamentoDoDia], **kwargs: object):  # type: ignore[no-untyped-def]
    return calcular_metricas(agendamentos, AGENDAS, REGRAS, INICIO, FIM, **kwargs)  # type: ignore[arg-type]


def dia_tipico() -> list[AgendamentoDoDia]:
    return [
        atendimento(1, hora_agendada="08:00", chegada="07:50", chamada="08:05", fim="08:20"),
        atendimento(2, hora_agendada="08:30", chegada="08:20", chamada="08:40", fim="09:00"),
        atendimento(
            3,
            id_agenda=20,
            agenda_nome="Audiometria",
            hora_agendada="13:30",
            chegada="13:25",
            chamada="13:35",
            fim="13:45",
        ),
        com_situacao(
            4, Situacao.FALTOU, id_agenda=20, agenda_nome="Audiometria", hora_agendada="14:00"
        ),
        com_situacao(5, Situacao.CANCELADO, hora_agendada="09:00"),
        com_situacao(6, Situacao.AGENDADO, hora_agendada="15:00"),
    ]


class TestDiaTipico:
    def test_kpis(self) -> None:
        k = calcular(dia_tipico()).kpis
        assert k.agendados == 5  # cancelado fica fora da base
        assert k.cancelados == 1
        assert k.atendimentos == 3
        assert k.faltas == 1
        assert k.taxa_faltas == pytest.approx(0.2)
        assert k.sem_baixa == 1
        assert k.espera_media_s == 900  # (15 + 20 + 10) / 3 min
        assert k.espera_mediana_s == 900
        assert k.espera_maxima_s == 1200
        assert k.tma_s == 900
        assert k.atendimentos_medidos == 3
        assert k.sem_turno == 0

    def test_por_turno(self) -> None:
        m = calcular(dia_tipico())
        manha, tarde = m.por_turno["manha"], m.por_turno["tarde"]
        assert (manha.rotulo, manha.faixa) == ("Manhã", "06:00 às 12:59")
        assert (tarde.rotulo, tarde.faixa) == ("Tarde", "13:00 às 18:00")
        assert (manha.agendados, manha.atendimentos, manha.faltas) == (2, 2, 0)
        assert manha.espera_media_s == 1050
        assert manha.espera_maxima_s == 1200
        assert manha.tma_s == 1050
        assert (tarde.agendados, tarde.atendimentos, tarde.faltas) == (3, 1, 1)
        assert tarde.taxa_faltas == pytest.approx(1 / 3)
        assert tarde.tma_s == 600

    def test_tma_por_consultorio_ordenado_do_maior_para_o_menor(self) -> None:
        linhas = calcular(dia_tipico()).consultorios
        assert [c.consultorio for c in linhas] == ["Sala 01", "Audiometria"]
        sala = linhas[0]
        assert sala.agenda == "Clínico"
        assert (sala.manha.atendimentos, sala.manha.tma_s) == (2, 1050)
        assert (sala.tarde.atendimentos, sala.tarde.tma_s) == (0, None)
        assert (sala.total.atendimentos, sala.total.tma_s) == (2, 1050)

    def test_tempo_por_agenda(self) -> None:
        agendas = calcular(dia_tipico()).agendas
        clinico = agendas[0]
        assert clinico.agenda == "Clínico"
        assert (clinico.agendados, clinico.atendimentos) == (3, 2)
        assert (clinico.media_s, clinico.mediana_s, clinico.maximo_s) == (1050, 1050, 1200)
        assert agendas[1].agenda == "Audiometria"

    def test_alertas_sem_baixa(self) -> None:
        alertas = calcular(dia_tipico()).alertas
        assert alertas.sem_baixa.ids == (6,)
        assert alertas.atipicos == ()
        assert alertas.quantidade == 1


class TestTurnos:
    def test_atendimento_cruzando_12_59_para_13_00_usa_hora_agendada(self) -> None:
        m = calcular(
            [
                # agendado 12:59, atendido já na tarde → continua sendo da manhã
                atendimento(
                    1, hora_agendada="12:59", chegada="12:55", chamada="13:02", fim="13:20"
                ),
                atendimento(
                    2, hora_agendada="13:00", chegada="12:50", chamada="12:58", fim="13:10"
                ),
            ]
        )
        assert m.por_turno["manha"].atendimentos == 1
        assert m.por_turno["manha"].tma_s == 18 * 60
        assert m.por_turno["tarde"].atendimentos == 1
        assert m.por_turno["tarde"].tma_s == 12 * 60

    def test_sem_hora_agendada_usa_chegada(self) -> None:
        ag = atendimento(1, hora_agendada=None, chegada="13:10", chamada="13:20", fim="13:30")
        assert definir_turno(ag, ConfiguracaoTurnos(), REGRAS.fuso) is Turno.TARDE

    def test_sem_hora_e_sem_chegada_usa_primeiro_evento_do_dia(self) -> None:
        ag = com_situacao(1, Situacao.FALTOU, hora_agendada=None)
        # primeiro evento do dia é às 06:00 (criação) → manhã
        assert definir_turno(ag, ConfiguracaoTurnos(), REGRAS.fuso) is Turno.MANHA

    def test_sem_hora_e_sem_eventos_fica_sem_turno_mas_conta_no_total(self) -> None:
        ag = AgendamentoDoDia(
            id_agendamento=1,
            id_agenda=10,
            agenda_nome="Clínico",
            id_unidade_atendimento=1,
            data_agendamento=DIA,
            hora_agendamento=None,
            situacao_atual=Situacao.FALTOU,
        )
        m = calcular([ag])
        assert m.kpis.faltas == 1
        assert m.kpis.sem_turno == 1
        assert m.por_turno["manha"].faltas + m.por_turno["tarde"].faltas == 0


class TestAtipicos:
    def test_menor_que_1_e_maior_que_180_min_contam_mas_ficam_fora_do_tma(self) -> None:
        m = calcular(
            [
                atendimento(1, hora_agendada="08:00", chamada="08:05", fim="08:05:30"),
                atendimento(2, hora_agendada="08:10", chamada="08:10", fim="11:11"),
                atendimento(3, hora_agendada="08:20", chamada="08:20", fim="08:30"),
            ]
        )
        assert m.kpis.atendimentos == 3
        assert m.kpis.atendimentos_medidos == 1
        assert m.kpis.tma_s == 600
        assert m.consultorios[0].total.tma_s == 600
        assert m.agendas[0].maximo_s == 600
        motivos = {a.id_agendamento: (a.motivo, a.duracao_s) for a in m.alertas.atipicos}
        assert motivos == {1: ("menos de 1 min", 30), 2: ("mais de 180 min", 181 * 60)}
        assert m.alertas.atipicos[0].consultorio == "Sala 01"
        assert m.alertas.atipicos[0].hora_agendada == "08:00"

    @pytest.mark.parametrize(
        ("minutos", "esperado"),
        [(0.99, "menos de 1 min"), (1, None), (180, None), (180.01, "mais de 180 min")],
    )
    def test_limites_sao_inclusivos(self, minutos: float, esperado: str | None) -> None:
        assert classificar_atipico(timedelta(minutes=minutos), REGRAS) == esperado

    def test_limites_configuraveis(self) -> None:
        regras = RegrasMetricas(atipico_min=timedelta(minutes=2), atipico_max=timedelta(hours=1))
        assert classificar_atipico(timedelta(seconds=90), regras) == "menos de 2 min"
        assert classificar_atipico(timedelta(minutes=61), regras) == "mais de 60 min"

    def test_limites_invalidos(self) -> None:
        with pytest.raises(ValueError, match="atípico"):
            RegrasMetricas(atipico_min=timedelta(minutes=5), atipico_max=timedelta(minutes=5))


class TestSaltoDeStatus:
    def test_aguardando_direto_para_atendido_conta_sem_tempo_medido(self) -> None:
        ag = AgendamentoDoDia(
            id_agendamento=7,
            id_agenda=10,
            agenda_nome="Clínico",
            id_unidade_atendimento=1,
            data_agendamento=DIA,
            hora_agendamento=time(9, 0),
            situacao_atual=Situacao.ATENDIDO,
            eventos=(
                evento(7, None, Situacao.AGENDADO, "06:00"),
                evento(7, Situacao.AGENDADO, Situacao.AGUARDANDO, "08:50"),
                evento(7, Situacao.AGUARDANDO, Situacao.ATENDIDO, "09:30"),
            ),
        )
        m = calcular(
            [
                ag,
                atendimento(
                    8, hora_agendada="09:10", chegada="08:55", chamada="09:10", fim="09:20"
                ),
            ]
        )
        assert m.kpis.atendimentos == 2
        assert m.kpis.atendimentos_medidos == 1
        assert m.kpis.tma_s == 600
        assert m.kpis.espera_media_s == 15 * 60  # só o agendamento 8 tem espera medida
        assert m.alertas.sem_tempo_medido.ids == (7,)
        assert m.alertas.atipicos == ()

    def test_primeira_observacao_ja_atendido(self) -> None:
        ag = AgendamentoDoDia(
            id_agendamento=9,
            id_agenda=10,
            agenda_nome="Clínico",
            id_unidade_atendimento=1,
            data_agendamento=DIA,
            hora_agendamento=time(9, 0),
            situacao_atual=Situacao.ATENDIDO,
            eventos=(evento(9, None, Situacao.ATENDIDO, "09:30"),),
        )
        m = calcular([ag])
        assert m.kpis.atendimentos == 1
        assert m.kpis.tma_s is None
        assert m.alertas.sem_tempo_medido.total == 1


class TestCasosEspeciais:
    def test_dia_sem_agendamentos(self) -> None:
        m = calcular([])
        assert m.sem_movimento
        assert m.kpis == Kpis(
            agendados=0,
            cancelados=0,
            atendimentos=0,
            faltas=0,
            taxa_faltas=None,
            sem_baixa=0,
            espera_media_s=None,
            espera_mediana_s=None,
            espera_maxima_s=None,
            tma_s=None,
            atendimentos_medidos=0,
            sem_turno=0,
        )
        assert m.consultorios == ()
        assert m.agendas == ()
        assert m.por_turno["manha"].taxa_faltas is None
        assert m.alertas.quantidade == 0

    def test_so_cancelamentos_e_sem_movimento(self) -> None:
        m = calcular([com_situacao(1, Situacao.CANCELADO)])
        assert m.sem_movimento
        assert m.kpis.cancelados == 1

    def test_paciente_em_duas_agendas_conta_duas_passagens(self) -> None:
        m = calcular(
            [
                atendimento(100, id_agenda=10, hora_agendada="08:00", chamada="08:05", fim="08:20"),
                atendimento(
                    101,
                    id_agenda=20,
                    agenda_nome="Audiometria",
                    hora_agendada="08:30",
                    chegada="08:21",
                    chamada="08:25",
                    fim="08:35",
                ),
            ]
        )
        assert m.kpis.atendimentos == 2
        assert {c.consultorio: c.total.atendimentos for c in m.consultorios} == {
            "Sala 01": 1,
            "Audiometria": 1,
        }

    def test_falta_registrada_na_agenda_conta_como_falta_e_nao_sem_baixa(self) -> None:
        agendamentos = [
            com_situacao(1, Situacao.AGENDADO, hora_agendada="10:00"),
            atendimento(2, hora_agendada="14:00", chegada="13:50", chamada=None, fim=None),
            com_situacao(3, Situacao.AGENDADO, hora_agendada="15:00"),
        ]
        m = calcular(agendamentos, faltas_confirmadas={1, 2})
        assert m.kpis.faltas == 2
        assert m.kpis.sem_baixa == 1
        assert m.alertas.sem_baixa.ids == (3,)
        assert m.alertas.faltas_confirmadas_na_agenda.ids == (1, 2)
        assert m.por_turno["manha"].faltas == 1
        assert m.por_turno["tarde"].faltas == 1

    def test_falta_confirmada_nao_altera_quem_ja_foi_atendido(self) -> None:
        m = calcular([atendimento(1)], faltas_confirmadas={1})
        assert m.kpis.atendimentos == 1
        assert m.kpis.faltas == 0

    def test_em_atendimento_no_fim_do_dia_vira_alerta(self) -> None:
        m = calcular([atendimento(1, fim=None)])
        assert m.alertas.em_atendimento_aberto.ids == (1,)
        assert m.kpis.atendimentos == 0
        assert m.kpis.sem_baixa == 0
        assert m.kpis.espera_media_s == 15 * 60

    def test_agenda_desconhecida_usa_nome_do_agendamento(self) -> None:
        m = calcular([atendimento(1, id_agenda=None, agenda_nome="Raio X")])
        assert m.consultorios[0].consultorio == "Raio X"
        assert m.consultorios[0].id_agenda is None

    def test_falhas_de_coleta_e_verificacao_repassadas(self) -> None:
        falha = FalhaColeta(hora("10:12"), hora("10:40"))
        m = calcular([], falhas_coleta=[falha], verificacao_faltas_indisponivel=True)
        assert m.alertas.falhas_coleta == (falha,)
        assert falha.minutos == 28
        assert m.alertas.verificacao_faltas_indisponivel
        assert m.alertas.quantidade == 2

    def test_consultorio_sem_tma_vai_para_o_fim(self) -> None:
        m = calcular(
            [
                com_situacao(1, Situacao.FALTOU, id_agenda=20, agenda_nome="Audiometria"),
                atendimento(2, chamada="08:00", fim="08:05"),
            ]
        )
        assert [c.consultorio for c in m.consultorios] == ["Sala 01", "Audiometria"]
        assert m.consultorios[1].total.tma_s is None


class TestReconstrucaoPorEventos:
    def test_situacao_no_instante(self) -> None:
        ag = atendimento(1, chegada="08:00", chamada="08:10", fim="08:20")
        assert situacao_em(ag, hora("08:05")) is Situacao.AGUARDANDO
        assert situacao_em(ag, hora("08:15")) is Situacao.EM_ATENDIMENTO
        assert situacao_em(ag, hora("23:00")) is Situacao.ATENDIDO
        assert situacao_em(ag, hora("05:00")) is Situacao.AGENDADO

    def test_sem_eventos_usa_situacao_atual(self) -> None:
        ag = AgendamentoDoDia(1, 10, "Clínico", 1, DIA, time(8), Situacao.FALTOU)
        assert situacao_em(ag, hora("23:00")) is Situacao.FALTOU

    def test_fechamento_parcial_do_dia_ignora_eventos_posteriores(self) -> None:
        """A mesma função atende o tempo real: fim = agora."""
        ag = atendimento(1, chegada="08:00", chamada="08:10", fim="08:20")
        m = calcular_metricas([ag], AGENDAS, REGRAS, INICIO, hora("08:15"))
        assert m.kpis.atendimentos == 0
        assert m.alertas.em_atendimento_aberto.ids == (1,)
        assert m.kpis.espera_media_s == 600

    def test_retorno_para_espera_mede_primeira_espera_e_ultimo_atendimento(self) -> None:
        eventos = (
            evento(1, None, Situacao.AGENDADO, "06:00"),
            evento(1, Situacao.AGENDADO, Situacao.AGUARDANDO, "08:00"),
            evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:10"),
            evento(1, Situacao.EM_ATENDIMENTO, Situacao.AGUARDANDO, "08:15"),
            evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:30"),
            evento(1, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "08:45"),
        )
        tempos = medir_tempos(eventos, hora("23:00"))
        assert tempos.espera == timedelta(minutes=10)
        assert tempos.atendimento == timedelta(minutes=15)

    def test_consultorio_mede_a_ultima_espera(self) -> None:
        """Chamado no guichê e devolvido à espera: conta só a espera pelo consultório."""
        eventos = (
            evento(1, None, Situacao.AGENDADO, "06:00"),
            evento(1, Situacao.AGENDADO, Situacao.AGUARDANDO, "08:00"),
            evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:05"),  # guichê
            evento(1, Situacao.EM_ATENDIMENTO, Situacao.AGUARDANDO, "08:10"),
            evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:40"),  # médico
            evento(1, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "08:55"),
        )
        tempos = medir_tempos(eventos, hora("23:00"), ultima_espera=True)
        assert tempos.espera == timedelta(minutes=30)
        assert tempos.atendimento == timedelta(minutes=15)
        # Ainda aguardando o médico: a espera do consultório não terminou.
        assert medir_tempos(eventos, hora("08:20"), ultima_espera=True).espera is None

    def test_chamada_sem_passar_pela_espera_nao_mede_espera(self) -> None:
        eventos = (
            evento(1, None, Situacao.AGENDADO, "06:00"),
            evento(1, Situacao.AGENDADO, Situacao.EM_ATENDIMENTO, "08:10"),
            evento(1, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "08:20"),
        )
        tempos = medir_tempos(eventos, hora("23:00"))
        assert tempos.espera is None
        assert tempos.atendimento == timedelta(minutes=10)

    def test_eventos_fora_de_ordem_sao_ordenados(self) -> None:
        eventos = (
            evento(1, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "08:20"),
            evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:10"),
            evento(1, Situacao.AGENDADO, Situacao.AGUARDANDO, "08:00"),
        )
        tempos = medir_tempos(eventos, hora("23:00"))
        assert tempos.espera == timedelta(minutes=10)
        assert tempos.atendimento == timedelta(minutes=10)


def test_kpis_de_dict_tolera_campos_ausentes() -> None:
    k = Kpis.de_dict({"atendimentos": 10, "taxa_faltas": 0.1, "tma_s": 600.4, "x": "y"})
    assert k.atendimentos == 10
    assert k.faltas == 0
    assert k.taxa_faltas == 0.1
    assert k.tma_s == 600
    assert k.espera_media_s is None


AGENDAS_COM_GUICHE = {
    10: agenda(10, "Clínico", sala="Sala 01"),
    50: agenda(50, "Recepção", sala="Guichê 1", guiche=True),
}


class TestGuichesETurnos:
    """Guichês separados na análise por turno; consultórios e agendas por turno."""

    def calcular(self, agendamentos: list[AgendamentoDoDia]):  # type: ignore[no-untyped-def]
        return calcular_metricas(agendamentos, AGENDAS_COM_GUICHE, REGRAS, INICIO, FIM)

    def dia(self) -> list[AgendamentoDoDia]:
        return [
            # Sala 01: médico da manhã atende em 15 min; o da tarde, em 30 min.
            atendimento(1, hora_agendada="08:00", chegada="07:50", chamada="08:00", fim="08:15"),
            atendimento(2, hora_agendada="14:00", chegada="13:50", chamada="14:00", fim="14:30"),
            # Guichê 1: só de manhã, 5 min.
            atendimento(
                3,
                id_agenda=50,
                agenda_nome="Recepção",
                hora_agendada="07:30",
                chegada="07:25",
                chamada="07:30",
                fim="07:35",
            ),
        ]

    def test_por_turno_separa_guiches_das_demais_agendas(self) -> None:
        m = self.calcular(self.dia())
        agendas, guiches = m.por_turno_grupos["agendas"], m.por_turno_grupos["guiches"]
        assert (agendas["manha"].atendimentos, agendas["tarde"].atendimentos) == (1, 1)
        assert (guiches["manha"].atendimentos, guiches["tarde"].atendimentos) == (1, 0)
        assert guiches["manha"].tma_s == 5 * 60
        assert m.por_turno["manha"].atendimentos == 2  # o total continua com todos

    def test_consultorio_por_turno_diferencia_os_medicos(self) -> None:
        m = self.calcular(self.dia())
        manha = {c.consultorio: c.tma_s for c in m.consultorios_por_turno["manha"]}
        tarde = {c.consultorio: c.tma_s for c in m.consultorios_por_turno["tarde"]}
        assert manha == {"Guichê 1": 5 * 60, "Sala 01": 15 * 60}
        assert tarde == {"Sala 01": 30 * 60}  # guichê sem agendamento à tarde não aparece
        assert [c.consultorio for c in m.consultorios_por_turno["manha"]] == [
            "Sala 01",
            "Guichê 1",
        ]  # do maior para o menor TMA
        assert m.consultorios_por_turno["manha"][1].guiche is True

    def test_agendas_por_turno(self) -> None:
        m = self.calcular(self.dia())
        tarde = m.agendas_por_turno["tarde"]
        assert [(a.agenda, a.atendimentos, a.media_s) for a in tarde] == [("Clínico", 1, 1800)]
        assert {a.agenda for a in m.agendas_por_turno["manha"]} == {"Clínico", "Recepção"}

    def test_kpis_separam_consultorios_e_recepcao(self) -> None:
        k = self.calcular(self.dia()).kpis
        assert (k.atendimentos, k.atendimentos_consultorios, k.atendimentos_guiches) == (3, 2, 1)
        assert k.espera_recepcao_s == 5 * 60  # só a espera no guichê
        assert k.espera_consultorio_s == 10 * 60  # 07:50→08:00 e 13:50→14:00
        assert (k.tma_consultorios_s, k.tma_guiches_s) == (round(22.5 * 60), 5 * 60)
        assert k.tem_guiche

    def test_espera_do_consultorio_nao_inclui_a_passagem_pelo_guiche(self) -> None:
        """Agendamento do consultório chamado no guichê e devolvido à espera do médico."""
        ida = atendimento(4, hora_agendada="09:00", chegada="08:30", chamada="08:35", fim=None)
        volta = (
            evento(4, Situacao.EM_ATENDIMENTO, Situacao.AGUARDANDO, "08:40"),
            evento(4, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "09:00"),
            evento(4, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "09:15"),
        )
        passou_no_guiche = replace(ida, eventos=(*ida.eventos, *volta))
        k = self.calcular([*self.dia(), passou_no_guiche]).kpis
        # (10 + 10 + 20) / 3: a espera do agendamento 4 é 08:40→09:00, não 08:30→08:35.
        assert k.espera_consultorio_s == round(40 * 60 / 3)
        assert k.espera_recepcao_s == 5 * 60

    def test_sem_guiche_nao_ha_separacao(self) -> None:
        k = calcular(dia_tipico()).kpis
        assert not k.tem_guiche
        assert k.atendimentos_consultorios is None
        assert k.espera_recepcao_s is None
