"""Tópicos do relatório (salas de exames, consultórios, guichês) e detalhe por tipo de exame."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from relatorio.domain.dashboard import resumir_periodo
from relatorio.domain.deteccao import Deteccao, detectar_transicao
from relatorio.domain.entidades import OrigemEvento, Situacao, Snapshot, TipoAgenda
from relatorio.domain.metricas import (
    OUTROS_TIPOS,
    TIPOS_ATENDIMENTO,
    AgendamentoDoDia,
    RegrasMetricas,
    calcular_metricas,
    tipo_de_exame,
)
from relatorio.domain.resumo import para_json
from tests.fabricas import agenda, atendimento, com_situacao, hora, registro_sgg

AGENDAS = {
    10: agenda(10, "Clínico", sala="Consultório 1"),
    20: agenda(20, "Audiometria", sala="Sala 2", tipo=TipoAgenda.SALA_EXAMES),
    50: agenda(50, "Recepção", sala="Guichê 1", guiche=True),
}


def calcular(agendamentos: list[AgendamentoDoDia]):  # type: ignore[no-untyped-def]
    return calcular_metricas(
        agendamentos, AGENDAS, RegrasMetricas(), hora("00:00"), hora("23:59:59")
    )


def dia() -> list[AgendamentoDoDia]:
    return [
        # Consultório: admissional (espera 10, consulta 15) e periódico (espera 20, consulta 25).
        atendimento(1, chegada="07:50", chamada="08:00", fim="08:15", tipo_exame="Admissional"),
        atendimento(2, chegada="08:00", chamada="08:20", fim="08:45", tipo_exame="periódico"),
        com_situacao(3, Situacao.FALTOU, tipo_exame="Demissional"),
        com_situacao(4, Situacao.CANCELADO, tipo_exame="Admissional"),  # fora de tudo
        # Sala de exames: admissional (espera 5, exame 10) e mudança de função (sem tipo novo).
        atendimento(
            5,
            id_agenda=20,
            chegada="08:00",
            chamada="08:05",
            fim="08:15",
            tipo_exame="Admissional",
        ),
        atendimento(
            6,
            id_agenda=20,
            chegada="08:00",
            chamada="08:10",
            fim="08:30",
            tipo_exame="Mudança de Função",
        ),
        # Guichê: retorno ao trabalho e um sem tipo (vai para "Outros").
        atendimento(
            7, id_agenda=50, chegada="07:40", chamada="07:42", fim="07:45", tipo_exame="Retorno"
        ),
        atendimento(8, id_agenda=50, chegada="07:40", chamada="07:44", fim="07:50"),
    ]


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        ("Admissional", "Admissional"),
        ("DEMISSIONAL", "Demissional"),
        ("periodico", "Periódico"),
        ("Mudança de Riscos Ocupacionais", "Mudança de Riscos Ocupacionais"),
        ("Mudança de Função", "Mudança de Riscos Ocupacionais"),
        ("Retorno ao trabalho", "Retorno ao Trabalho"),
        ("", OUTROS_TIPOS),
        ("Consulta", OUTROS_TIPOS),
    ],
)
def test_tipo_de_exame_normaliza_o_texto_do_sgg(texto: str, tipo: str) -> None:
    assert tipo_de_exame(texto) == tipo


def test_cada_agenda_vai_para_o_topico_do_seu_tipo() -> None:
    t = calcular(dia()).por_topico
    assert list(t) == ["sala_exames", "consultorio", "guiche"]
    consultorio, sala, guiche = t["consultorio"].total, t["sala_exames"].total, t["guiche"].total
    assert (consultorio.agendados, consultorio.atendimentos, consultorio.faltas) == (3, 2, 1)
    assert (consultorio.espera_media_s, consultorio.tma_s) == (15 * 60, 20 * 60)
    assert (sala.agendados, sala.espera_media_s, sala.tma_s) == (2, round(7.5 * 60), 15 * 60)
    assert (guiche.atendimentos, guiche.espera_media_s, guiche.tma_s) == (
        2,
        3 * 60,
        round(4.5 * 60),
    )
    assert consultorio.rotulo == "Consultório"


def test_detalhe_por_tipo_de_exame_em_cada_topico() -> None:
    t = calcular(dia()).por_topico
    consultorio = t["consultorio"].por_tipo
    assert list(consultorio) == list(TIPOS_ATENDIMENTO)  # sem "Outros": todos têm tipo
    assert (consultorio["Admissional"].atendimentos, consultorio["Admissional"].tma_s) == (
        1,
        15 * 60,
    )
    assert consultorio["Admissional"].agendados == 1  # o cancelado não conta
    assert (consultorio["Periódico"].espera_media_s, consultorio["Periódico"].tma_s) == (
        20 * 60,
        25 * 60,
    )
    assert (consultorio["Demissional"].agendados, consultorio["Demissional"].faltas) == (1, 1)
    assert consultorio["Retorno ao Trabalho"].agendados == 0  # zerado, mas presente

    sala = t["sala_exames"].por_tipo
    assert sala["Mudança de Riscos Ocupacionais"].tma_s == 20 * 60
    assert sala["Admissional"].espera_media_s == 5 * 60

    guiche = t["guiche"].por_tipo
    assert list(guiche) == [*TIPOS_ATENDIMENTO, OUTROS_TIPOS]
    assert (guiche["Retorno ao Trabalho"].atendimentos, guiche[OUTROS_TIPOS].atendimentos) == (
        1,
        1,
    )


def test_subtotais_somam_o_total_do_topico() -> None:
    for topico in calcular(dia()).por_topico.values():
        tipos = topico.por_tipo.values()
        assert sum(r.agendados for r in tipos) == topico.total.agendados
        assert sum(r.atendimentos for r in tipos) == topico.total.atendimentos
        assert sum(r.faltas for r in tipos) == topico.total.faltas


def test_agenda_desconhecida_conta_como_consultorio() -> None:
    sem_cadastro = replace(atendimento(9, tipo_exame="Admissional"), id_agenda=99)
    t = calcular([sem_cadastro]).por_topico
    assert t["consultorio"].total.atendimentos == 1
    assert t["sala_exames"].total.agendados == t["guiche"].total.agendados == 0


def test_tipo_do_sgg_fica_no_snapshot_e_mudanca_de_tipo_atualiza() -> None:
    registro = replace(registro_sgg(1, Situacao.AGENDADO, "07:00"), tipo="Periódico")

    def processar(atual: Snapshot | None) -> Deteccao:
        return detectar_transicao(
            registro, atual, id_agenda=10, observado_em=hora("07:01"), origem=OrigemEvento.POLLING
        )

    primeiro = processar(None).snapshot
    assert primeiro is not None and primeiro.tipo_exame == "Periódico"
    assert processar(primeiro).snapshot is None  # nada mudou
    # Snapshot gravado antes da coluna existir (tipo vazio): atualiza sem gerar evento.
    d = processar(replace(primeiro, tipo_exame=""))
    assert d.evento is None and d.snapshot is not None and d.snapshot.tipo_exame == "Periódico"


def test_dashboard_soma_os_topicos_dos_dias_ponderando_os_tempos() -> None:
    def dia_json(agendamentos: list[AgendamentoDoDia]) -> dict[str, Any]:
        m = calcular(agendamentos)
        return {"kpis": para_json(m.kpis), "por_topico": para_json(m.por_topico)}

    um = dia_json(dia())
    # Segundo dia: um admissional no consultório com 35 min de consulta.
    dois = dia_json([atendimento(1, chamada="08:00", fim="08:35", tipo_exame="Admissional")])
    antigo = {"kpis": {"agendados": 1}}  # resumo da regra 1.3: sem tópicos, não entra
    p = resumir_periodo(
        date(2026, 9, 23),
        date(2026, 9, 25),
        [(date(2026, 9, 23), um), (date(2026, 9, 24), dois), (date(2026, 9, 25), antigo)],
    )
    consultorio = p.por_topico["consultorio"]
    assert (consultorio.total.agendados, consultorio.total.atendimentos) == (4, 3)
    assert consultorio.total.tma_s == round((20 * 60 * 2 + 35 * 60) / 3)
    admissional = consultorio.por_tipo["Admissional"]
    assert (admissional.atendimentos, admissional.tma_s) == (2, 25 * 60)
    assert OUTROS_TIPOS in p.por_topico["guiche"].por_tipo
    assert OUTROS_TIPOS not in consultorio.por_tipo
