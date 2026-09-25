"""Situação ao vivo: fila de agora e movimento por hora (monitor do gerente)."""

from __future__ import annotations

from dataclasses import replace

from relatorio.domain.ao_vivo import calcular_ao_vivo
from relatorio.domain.entidades import Situacao
from tests.fabricas import atendimento, com_situacao, evento, hora


def test_fila_de_agora() -> None:
    agendamentos = [
        atendimento(1, chegada="07:50", chamada="08:05", fim="08:20"),
        atendimento(2, chegada="09:30", chamada=None, fim=None),
        atendimento(3, chegada="09:50", chamada=None, fim=None),
        atendimento(4, chegada="09:00", chamada="09:40", fim=None),
        # Termina depois do instante consultado: às 10:00 ainda está em atendimento.
        atendimento(5, chegada="09:45", chamada="09:55", fim="10:30"),
        com_situacao(6, Situacao.AGENDADO, hora_agendada="09:00"),
        com_situacao(7, Situacao.AGENDADO, hora_agendada="11:00"),
        com_situacao(8, Situacao.CANCELADO, quando="08:00"),
    ]
    v = calcular_ao_vivo(agendamentos, hora("10:00"))

    assert (v.aguardando, v.espera_atual_maxima_s, v.espera_atual_media_s) == (2, 1800, 1200)
    assert (v.em_atendimento, v.atendimento_atual_maximo_s) == (2, 1200)
    assert (v.a_chegar, v.a_chegar_atrasados) == (2, 1)

    por_hora = {h.hora: (h.chegadas, h.atendidos) for h in v.por_hora}
    assert list(por_hora) == list(range(6, 18))
    assert por_hora[7] == (1, 0)
    assert por_hora[8] == (0, 1)
    assert por_hora[9] == (4, 0)
    assert por_hora[10] == (0, 0)  # o fim das 10:30 ainda não aconteceu


def test_atraso_so_depois_da_tolerancia() -> None:
    agendado = com_situacao(1, Situacao.AGENDADO, hora_agendada="09:50")
    assert calcular_ao_vivo([agendado], hora("10:05")).a_chegar_atrasados == 0
    assert calcular_ao_vivo([agendado], hora("10:06")).a_chegar_atrasados == 1


def test_movimento_fora_da_janela_entra_nas_pontas() -> None:
    cedo = atendimento(1, chegada="05:40", chamada="05:50", fim="06:10")
    tarde = atendimento(2, hora_agendada="17:30", chegada="17:55", chamada="18:10", fim="18:40")
    v = calcular_ao_vivo([cedo, tarde], hora("19:00"))
    por_hora = {h.hora: (h.chegadas, h.atendidos) for h in v.por_hora}
    assert por_hora[6] == (1, 1)
    assert por_hora[17] == (1, 1)
    assert sum(c for c, _ in por_hora.values()) == 2


def test_fila_vazia() -> None:
    v = calcular_ao_vivo([], hora("08:00"))
    assert (v.aguardando, v.em_atendimento, v.a_chegar) == (0, 0, 0)
    assert v.espera_atual_maxima_s is None
    assert v.atendimento_atual_maximo_s is None
    assert all(h.chegadas == h.atendidos == 0 for h in v.por_hora)


def test_volta_para_a_espera_conta_do_novo_aguardando() -> None:
    """Chamado e devolvido à recepção: a espera atual recomeça na volta."""
    ida = atendimento(1, chegada="08:00", chamada="08:10", fim=None)
    volta = evento(1, Situacao.EM_ATENDIMENTO, Situacao.AGUARDANDO, "08:20")
    agendamento = replace(ida, eventos=(*ida.eventos, volta))
    v = calcular_ao_vivo([agendamento], hora("08:30"))
    assert (v.aguardando, v.espera_atual_maxima_s) == (1, 600)
    assert {h.hora: h.chegadas for h in v.por_hora}[8] == 1  # a chegada conta uma vez


def test_espera_separada_entre_recepcao_e_consultorio() -> None:
    guiche = 50
    agendamentos = [
        # Recepção: um aguardando o guichê desde 09:40, outro sendo atendido desde 09:55.
        atendimento(1, id_agenda=guiche, chegada="09:40", chamada=None, fim=None),
        atendimento(2, id_agenda=guiche, chegada="09:45", chamada="09:55", fim=None),
        # Consultório: dois aguardando o médico (desde 09:30 e 09:50).
        atendimento(3, chegada="09:30", chamada=None, fim=None),
        atendimento(4, chegada="09:50", chamada=None, fim=None),
    ]
    v = calcular_ao_vivo(agendamentos, hora("10:00"), guiches={guiche})
    assert v.tem_guiche
    assert v.recepcao is not None and v.consultorio is not None
    assert (v.recepcao.aguardando, v.recepcao.espera_maxima_s) == (1, 20 * 60)
    assert (v.recepcao.em_atendimento, v.recepcao.atendimento_maximo_s) == (1, 5 * 60)
    assert (v.consultorio.aguardando, v.consultorio.espera_maxima_s) == (2, 30 * 60)
    assert v.consultorio.em_atendimento == 0
    assert (v.aguardando, v.em_atendimento) == (3, 1)  # o total continua somando tudo


def test_sem_guiche_nao_separa() -> None:
    v = calcular_ao_vivo([atendimento(1, chegada="09:40", chamada=None, fim=None)], hora("10:00"))
    assert (v.tem_guiche, v.recepcao, v.consultorio) == (False, None, None)
