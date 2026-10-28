"""Dashboard do atendimento: soma dos resumos diários de um período."""

from __future__ import annotations

from datetime import date
from typing import Any

from relatorio.domain.dashboard import resumir_periodo
from relatorio.interfaces.web.dashboard import periodo_anterior

INICIO, FIM = date(2026, 10, 1), date(2026, 10, 4)


def resumo(
    agendados: int,
    atendimentos: int,
    faltas: int,
    espera: int | None,
    tma: int | None,
    medidos: int,
    alertas: int = 0,
    **extras: Any,
) -> dict[str, Any]:
    turno = {
        "rotulo": "Manhã",
        "faixa": "06:00–12:00",
        "agendados": agendados,
        "atendimentos": atendimentos,
        "faltas": faltas,
        "espera_media_s": espera,
        "espera_maxima_s": espera,
        "tma_s": tma,
        "atendimentos_medidos": medidos,
    }
    return {
        "kpis": {
            "agendados": agendados,
            "atendimentos": atendimentos,
            "faltas": faltas,
            "taxa_faltas": faltas / agendados if agendados else None,
            "espera_media_s": espera,
            "espera_maxima_s": espera,
            "tma_s": tma,
            "atendimentos_medidos": medidos,
            **extras,
        },
        "por_turno": {"manha": turno},
        "alertas": {"quantidade": alertas},
    }


def test_soma_contagens_e_pondera_os_tempos() -> None:
    p = resumir_periodo(
        INICIO,
        FIM,
        [
            (date(2026, 10, 1), resumo(10, 8, 2, espera=600, tma=300, medidos=8, alertas=1)),
            (date(2026, 10, 2), resumo(30, 24, 6, espera=1200, tma=600, medidos=24, alertas=2)),
        ],
    )
    k = p.kpis
    assert (k.agendados, k.atendimentos, k.faltas) == (40, 32, 8)
    assert k.taxa_faltas == 8 / 40  # recalculada pelas somas, não média das taxas
    assert k.espera_media_s == round((600 * 8 + 1200 * 24) / 32)  # 1050, não 900
    assert k.tma_s == round((300 * 8 + 600 * 24) / 32)
    assert k.espera_maxima_s == 1200
    assert p.alertas == 3
    assert (p.dias_consolidados, p.ultimo_dia, p.vazio) == (2, date(2026, 10, 2), False)
    manha, tarde = p.por_turno
    assert (manha.rotulo, manha.atendimentos, manha.faltas, manha.tma_s) == ("Manhã", 32, 8, 525)
    assert (tarde.rotulo, tarde.atendimentos, tarde.tma_s) == ("Tarde", 0, None)


def test_dias_sem_movimento_ou_sem_resumo_viram_lacuna_e_nao_pesam() -> None:
    p = resumir_periodo(
        INICIO,
        FIM,
        [
            (date(2026, 10, 1), resumo(10, 8, 2, espera=600, tma=300, medidos=8)),
            (date(2026, 10, 3), resumo(0, 0, 0, espera=None, tma=None, medidos=0)),
            (date(2026, 9, 30), resumo(99, 99, 0, espera=9, tma=9, medidos=99)),  # fora
        ],
    )
    assert p.kpis.atendimentos == 8
    assert p.kpis.tma_s == 300
    assert [(d.data.day, d.atendimentos, d.faltas) for d in p.dias] == [
        (1, 8, 2),
        (2, None, None),
        (3, None, None),
        (4, None, None),
    ]


def test_permanencia_so_com_guiches_e_soma_das_partes() -> None:
    guiches = {
        "atendimentos_consultorios": 6,
        "atendimentos_guiches": 4,
        "espera_consultorio_s": 600,
        "espera_recepcao_s": 120,
        "tma_consultorios_s": 900,
        "tma_guiches_s": 180,
    }
    p = resumir_periodo(
        INICIO,
        FIM,
        [
            (date(2026, 10, 1), resumo(10, 10, 0, espera=300, tma=500, medidos=10, **guiches)),
            (date(2026, 10, 2), resumo(5, 5, 0, espera=300, tma=500, medidos=5)),  # sem guichês
        ],
    )
    assert (p.kpis.atendimentos_consultorios, p.kpis.atendimentos_guiches) == (6, 4)
    assert p.kpis.permanencia_total_s == 120 + 180 + 600 + 900
    sem = resumir_periodo(INICIO, FIM, [(date(2026, 10, 2), resumo(5, 5, 0, 300, 500, 5))])
    assert sem.kpis.permanencia_total_s is None
    assert sem.kpis.atendimentos_guiches is None


def test_periodo_vazio() -> None:
    p = resumir_periodo(INICIO, FIM, [])
    assert p.vazio
    assert p.ultimo_dia is None
    assert (p.kpis.atendimentos, p.kpis.taxa_faltas, p.kpis.tma_s) == (0, None, None)
    assert len(p.dias) == 4


def test_tma_por_grupo_pondera_pelos_atendimentos_medidos() -> None:
    def dia(tma_guiches: int, atendidos: int, medidos: int) -> dict[str, Any]:
        m = resumo(10, 10, 0, 60, 60, 10, atendimentos_guiches=atendidos, tma_guiches_s=tma_guiches)
        m["por_grupo"] = {"guiches": {"atendimentos_medidos": medidos}}
        return m

    p = resumir_periodo(
        INICIO, FIM, [(date(2026, 10, 1), dia(100, 50, 1)), (date(2026, 10, 2), dia(300, 10, 10))]
    )
    assert p.kpis.tma_guiches_s == round((100 * 1 + 300 * 10) / 11)  # não (100*50 + 300*10)/60


def test_periodo_anterior_tem_o_mesmo_numero_de_dias() -> None:
    assert periodo_anterior(date(2026, 10, 1), date(2026, 10, 27)) == (
        date(2026, 9, 1),
        date(2026, 9, 27),
    )
    assert periodo_anterior(date(2026, 3, 29), date(2026, 3, 31)) == (
        date(2026, 2, 28),
        date(2026, 3, 2),
    )
    # Mês inteiro: não invade o próprio mês (seriam 01/02–03/03).
    assert periodo_anterior(date(2026, 3, 1), date(2026, 3, 31)) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
    )
    assert periodo_anterior(date(2026, 10, 1), date(2026, 10, 31)) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
    )
