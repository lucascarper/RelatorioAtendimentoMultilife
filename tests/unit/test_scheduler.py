"""Funções dos jobs agendados (datas de referência, janela e retentativas)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from relatorio.application.modelos import StatusJob
from relatorio.config import Settings
from relatorio.domain.entidades import Situacao
from relatorio.infrastructure.jobs import ExecutorJobs
from relatorio.infrastructure.scheduler import JobsAgendados
from tests.fabricas import DIA, hora, registro_sgg
from tests.unit.conftest import Sistema


@pytest.fixture
def jobs(sistema: Sistema) -> JobsAgendados:
    container = SimpleNamespace(
        casos=sistema,  # expõe coletar, reconciliar, consolidar, enviar… como o CasosDeUso
        executor=ExecutorJobs(None, sistema.uow, sistema.relogio),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        relogio=sistema.relogio,
        uow=sistema.uow,
    )
    return JobsAgendados(container)  # type: ignore[arg-type]


def execucoes(sistema: Sistema, job: str) -> list[tuple[StatusJob, str | None]]:
    return [
        (e.status, e.detalhe.get("referencia"))
        for e in sistema.banco.execucoes.values()
        if e.job == job
    ]


def test_coleta_so_dentro_da_janela(sistema: Sistema, jobs: JobsAgendados) -> None:
    sistema.relogio.instante = hora("18:30")
    jobs.coletar_ciclo()
    assert execucoes(sistema, "coletar_ciclo") == []
    sistema.relogio.instante = hora("08:00")
    sistema.sgg.editados.append([registro_sgg(1, Situacao.AGUARDANDO, "07:59")])
    jobs.coletar_ciclo()
    assert execucoes(sistema, "coletar_ciclo") == [(StatusJob.SUCESSO, None)]


def test_reconciliacao_pula_retentativa_do_dia_ja_feito(
    sistema: Sistema, jobs: JobsAgendados
) -> None:
    sistema.relogio.instante = hora("18:30")
    jobs.reconciliar_dia(tentativa=1)
    sistema.relogio.instante = hora("19:00")
    jobs.reconciliar_dia(tentativa=2)
    assert execucoes(sistema, "reconciliar_dia") == [(StatusJob.SUCESSO, "2026-09-23")]


def test_consolidacao_envio_e_verificacao_usam_os_dias_certos(
    sistema: Sistema, jobs: JobsAgendados
) -> None:
    sistema.uow().destinatarios.adicionar("glauco@multilife.com.br", None)
    sistema.relogio.instante = hora("23:00")
    jobs.consolidar_dia(tentativa=1)
    sistema.relogio.instante = hora("02:00", DIA + timedelta(days=1))
    jobs.consolidar_dia(tentativa=2)  # retentativa: já consolidado, não recalcula
    assert [r for _, r in execucoes(sistema, "consolidar_dia")] == ["2026-09-23"] * 2
    assert sistema.banco.execucoes[2].detalhe["status"] == "ja_consolidado"

    sistema.relogio.instante = hora("07:59", DIA + timedelta(days=1))
    jobs.enviar_relatorio(tentativa=1)
    sistema.relogio.instante = hora("08:10", DIA + timedelta(days=1))
    jobs.verificar_envio()
    assert len(sistema.email.enviados) == 1
    assert execucoes(sistema, "verificar_envio") == [(StatusJob.SUCESSO, "2026-09-23")]


def test_sincronizacao_e_retencao_uma_vez_por_mes(sistema: Sistema, jobs: JobsAgendados) -> None:
    jobs.sync_agendas()
    sistema.relogio.instante = hora("03:00", DIA.replace(day=1))
    jobs.limpar_retencao()
    sistema.relogio.instante = hora("03:00", DIA.replace(day=2))
    jobs.limpar_retencao()  # dia 2 é só retentativa
    assert execucoes(sistema, "sync_agendas") == [(StatusJob.SUCESSO, None)]
    assert execucoes(sistema, "limpar_retencao") == [(StatusJob.SUCESSO, "2026-09")]
