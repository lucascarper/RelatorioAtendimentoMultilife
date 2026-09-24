"""Detecção de transição (snapshot → evento)."""

from __future__ import annotations

from relatorio.domain.deteccao import (
    Deteccao,
    detectar_transicao,
    eh_salto,
    resolver_ocorrido_em,
)
from relatorio.domain.entidades import AgendamentoSgg, OrigemEvento, Situacao, Snapshot
from tests.fabricas import hora, registro_sgg


def processar(
    registro: AgendamentoSgg, atual: Snapshot | None, observado: str = "08:01"
) -> Deteccao:
    return detectar_transicao(
        registro,
        atual,
        id_agenda=10,
        observado_em=hora(observado),
        origem=OrigemEvento.POLLING,
    )


def test_primeira_observacao_gera_evento_e_snapshot() -> None:
    d = processar(registro_sgg(1, Situacao.AGUARDANDO, "08:00:30"), None)
    assert d.evento is not None
    assert d.evento.status_anterior is None
    assert d.evento.status_novo is Situacao.AGUARDANDO
    assert d.evento.ocorrido_em == hora("08:00:30")
    assert d.evento.observado_em == hora("08:01")
    assert d.snapshot is not None
    assert d.snapshot.situacao_atual is Situacao.AGUARDANDO
    assert d.snapshot.id_agenda == 10
    assert not d.salto


def test_mesmo_registro_recebido_duas_vezes_nao_duplica_evento() -> None:
    registro = registro_sgg(1, Situacao.AGUARDANDO, "08:00:30")
    primeira = processar(registro, None)
    assert primeira.snapshot is not None
    segunda = processar(registro, primeira.snapshot, observado="08:02")
    assert segunda.evento is None
    assert segunda.snapshot is None


def test_mudanca_de_status_usa_data_hora_edicao() -> None:
    atual = processar(registro_sgg(1, Situacao.AGUARDANDO, "08:00:30"), None).snapshot
    d = processar(registro_sgg(1, Situacao.EM_ATENDIMENTO, "08:10:15"), atual, "08:11")
    assert d.evento is not None
    assert d.evento.status_anterior is Situacao.AGUARDANDO
    assert d.evento.status_novo is Situacao.EM_ATENDIMENTO
    assert d.evento.ocorrido_em == hora("08:10:15")
    assert not d.salto


def test_salto_de_etapa_e_sinalizado() -> None:
    atual = processar(registro_sgg(1, Situacao.AGUARDANDO, "08:00"), None).snapshot
    d = processar(registro_sgg(1, Situacao.ATENDIDO, "08:30"), atual, "08:31")
    assert d.evento is not None
    assert d.salto


def test_mudanca_so_de_horario_atualiza_snapshot_sem_evento() -> None:
    atual = processar(registro_sgg(1, Situacao.AGENDADO, "07:00"), None).snapshot
    d = processar(
        registro_sgg(1, Situacao.AGENDADO, "07:30", hora_agendada="09:00"), atual, "07:31"
    )
    assert d.evento is None
    assert d.snapshot is not None
    assert d.snapshot.hora_agendamento is not None
    assert d.snapshot.hora_agendamento.hour == 9


def test_mantem_id_agenda_quando_nao_resolvido() -> None:
    atual = processar(registro_sgg(1, Situacao.AGENDADO, "07:00"), None).snapshot
    d = detectar_transicao(
        registro_sgg(1, Situacao.AGUARDANDO, "08:00"),
        atual,
        id_agenda=None,
        observado_em=hora("08:01"),
        origem=OrigemEvento.RECONCILIACAO,
    )
    assert d.snapshot is not None
    assert d.snapshot.id_agenda == 10
    assert d.evento is not None
    assert d.evento.origem is OrigemEvento.RECONCILIACAO


class TestResolverOcorridoEm:
    def test_edicao_posterior_a_ultima_e_anterior_a_observacao(self) -> None:
        assert resolver_ocorrido_em(hora("08:10"), hora("08:00"), hora("08:11")) == hora("08:10")

    def test_edicao_antiga_usa_observacao(self) -> None:
        assert resolver_ocorrido_em(hora("07:59"), hora("08:00"), hora("08:11")) == hora("08:11")

    def test_edicao_no_futuro_usa_observacao(self) -> None:
        assert resolver_ocorrido_em(hora("09:00"), None, hora("08:11")) == hora("08:11")

    def test_sem_edicao_usa_observacao(self) -> None:
        assert resolver_ocorrido_em(None, None, hora("08:11")) == hora("08:11")


def test_eh_salto() -> None:
    assert eh_salto(Situacao.AGENDADO, Situacao.EM_ATENDIMENTO)
    assert eh_salto(None, Situacao.ATENDIDO)
    assert not eh_salto(Situacao.AGENDADO, Situacao.AGUARDANDO)
    assert not eh_salto(Situacao.AGENDADO, Situacao.FALTOU)
    assert not eh_salto(Situacao.EM_ATENDIMENTO, Situacao.AGUARDANDO)
