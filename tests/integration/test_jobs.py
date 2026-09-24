"""Executor de jobs (advisory lock + auditoria) e registro no APScheduler."""

from __future__ import annotations

from typing import Any

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Engine, text

from relatorio.application.modelos import StatusJob
from relatorio.application.ports import FabricaUoW
from relatorio.config import Settings
from relatorio.infrastructure.jobs import ExecutorJobs
from relatorio.infrastructure.scheduler import registrar_jobs
from relatorio.infrastructure.sgg.erros import ErroSggServidor
from tests.fabricas import hora
from tests.fakes import RelogioFixo

pytestmark = pytest.mark.integration


def test_sucesso_e_falha_sao_auditados(uow: FabricaUoW, engine: Engine) -> None:
    executor = ExecutorJobs(engine, uow, RelogioFixo(hora("08:00")))
    assert executor.executar("coletar_ciclo", lambda: {"eventos_novos": 3}) is StatusJob.SUCESSO

    chamadas: list[str] = []

    def falhar() -> dict[str, object]:
        raise ErroSggServidor("fora do ar", "S000")

    status = executor.executar("coletar_ciclo", falhar, ao_falhar=lambda: chamadas.append("x"))
    assert status is StatusJob.FALHA
    assert chamadas == ["x"]
    with uow() as u:
        [falha, sucesso] = u.execucoes.recentes_por_job()["coletar_ciclo"]
    assert sucesso.detalhe["eventos_novos"] == 3
    assert "duracao_ms" in sucesso.detalhe
    assert falha.detalhe["erro"] == "ErroSggServidor: S000: fora do ar"


def test_erro_inesperado_tambem_e_registrado(uow: FabricaUoW, engine: Engine) -> None:
    executor = ExecutorJobs(engine, uow, RelogioFixo(hora("08:00")))

    def quebrar() -> dict[str, object]:
        raise ZeroDivisionError("bug")

    def ao_falhar_quebrado() -> None:
        raise RuntimeError("alerta quebrou")

    assert executor.executar("x", quebrar, ao_falhar=ao_falhar_quebrado) is StatusJob.FALHA


def test_trava_ocupada_por_outro_container_ignora_job(uow: FabricaUoW, engine: Engine) -> None:
    executor = ExecutorJobs(engine, uow, RelogioFixo(hora("08:00")))
    executou: list[bool] = []
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as outro:
        outro.execute(text("SELECT pg_advisory_lock(hashtext('coletor'))"))
        status = executor.executar(
            "coletar_ciclo", lambda: executou.append(True) or {}, trava="coletor"
        )
        outro.execute(text("SELECT pg_advisory_unlock(hashtext('coletor'))"))
    assert status is StatusJob.IGNORADO
    assert executou == []
    # Liberada a trava, o job volta a rodar.
    assert executor.executar("coletar_ciclo", lambda: {}, trava="coletor") is StatusJob.SUCESSO


class JobsFalsos:
    def __getattr__(self, nome: str) -> Any:
        return lambda *a, **k: None


def _jobs(settings: Settings) -> dict[str, Any]:
    agendador = BlockingScheduler()
    registrar_jobs(agendador, JobsFalsos(), settings)  # type: ignore[arg-type]
    return {job.id: job for job in agendador.get_jobs()}


def _gatilhos(settings: Settings) -> dict[str, str]:
    return {id_job: str(job.trigger) for id_job, job in _jobs(settings).items()}


def test_agenda_de_jobs_da_documentacao() -> None:
    gatilhos = _gatilhos(Settings(_env_file=None))  # type: ignore[call-arg]
    assert set(gatilhos) == {
        "sync_agendas",
        "coletar_ciclo",
        "reconciliar_dia_1",
        "reconciliar_dia_2",
        "reconciliar_dia_3",
        "consolidar_dia_1",
        "consolidar_dia_2",
        "consolidar_dia_3",
        "enviar_relatorio_1",
        "enviar_relatorio_2",
        "enviar_relatorio_3",
        "verificar_envio",
        "limpar_retencao",
    }
    assert "hour='5', minute='30'" in gatilhos["sync_agendas"]
    assert "hour='6-18', minute='*', second='0'" in gatilhos["coletar_ciclo"]
    assert "hour='18', minute='30'" in gatilhos["reconciliar_dia_1"]
    assert "hour='23', minute='0'" in gatilhos["consolidar_dia_1"]
    assert "hour='2', minute='0'" in gatilhos["consolidar_dia_2"]
    assert "hour='7', minute='59'" in gatilhos["enviar_relatorio_1"]
    assert "hour='8', minute='3'" in gatilhos["enviar_relatorio_3"]
    assert "day='1-2', hour='3', minute='0'" in gatilhos["limpar_retencao"]
    jobs = _jobs(Settings(_env_file=None))  # type: ignore[call-arg]
    assert all(str(j.trigger.timezone) == "America/Sao_Paulo" for j in jobs.values())
    assert all(j.max_instances == 1 and j.coalesce for j in jobs.values())


def test_sem_coletor_proprio_so_le_eventos_do_painel() -> None:
    gatilhos = _gatilhos(Settings(_env_file=None, coletor_habilitado=False))  # type: ignore[call-arg]
    assert "coletar_ciclo" not in gatilhos
    assert "reconciliar_dia_1" not in gatilhos
    assert "consolidar_dia_1" in gatilhos
