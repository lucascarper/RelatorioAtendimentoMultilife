"""Repositórios PostgreSQL: UNIQUE, upserts, travas de envio e consultas de auditoria."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import date, timedelta

import pytest

from relatorio.application.modelos import ColetaExames, StatusEnvio, StatusJob
from relatorio.application.ports import FabricaUoW
from relatorio.domain.entidades import OrigemEvento, Situacao
from relatorio.domain.exames import ExameClinico, Medico
from tests.fabricas import DIA, agenda, evento, hora, snapshot
from tests.integration.conftest import ESTADO_INICIAL

pytestmark = pytest.mark.integration


def test_migracao_semeia_destinatarios_iniciais(uow: FabricaUoW) -> None:
    assert ESTADO_INICIAL["destinatarios"] == [
        "glauco@multilife.com.br",
        "tecnologia@multilife.com.br",
    ]


class TestEventos:
    def test_unique_garante_idempotencia(self, uow: FabricaUoW) -> None:
        e = evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:10:15")
        with uow() as u:
            assert u.eventos.inserir(e) is True
            assert u.eventos.inserir(e) is False
            u.commit()
        with uow() as u:
            assert u.eventos.inserir(e) is False
            eventos = u.eventos.listar_por_agendamentos([1])
        assert eventos[1] == [e]
        assert eventos[1][0].ocorrido_em == hora("08:10:15")

    def test_listagem_ordenada_e_retencao(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.eventos.inserir(evento(2, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "09:00"))
            u.eventos.inserir(evento(2, None, Situacao.AGUARDANDO, "08:00"))
            u.eventos.inserir(evento(3, None, Situacao.FALTOU, "10:00", DIA - timedelta(days=800)))
            u.commit()
        with uow() as u:
            assert [e.status_novo for e in u.eventos.listar_por_agendamentos([2, 3])[2]] == [
                Situacao.AGUARDANDO,
                Situacao.ATENDIDO,
            ]
            assert u.eventos.apagar_anteriores_a(hora("00:00", DIA - timedelta(days=730))) == 1
            u.commit()

    def test_sem_commit_desfaz(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.eventos.inserir(evento(4, None, Situacao.AGENDADO, "07:00"))
        with uow() as u:
            assert u.eventos.listar_por_agendamentos([4]) == {}


class TestSnapshots:
    def test_upsert_e_consulta_por_data(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.snapshots.salvar(snapshot(1, Situacao.AGENDADO))
            u.snapshots.salvar(snapshot(1, Situacao.AGUARDANDO, hora_agendamento=None))
            u.snapshots.salvar(snapshot(2, Situacao.FALTOU, data_agendamento=DIA - timedelta(1)))
            u.commit()
        with uow() as u:
            atual = u.snapshots.obter_varios([1, 2, 99])
            assert set(atual) == {1, 2}
            assert atual[1].situacao_atual is Situacao.AGUARDANDO
            assert atual[1].hora_agendamento is None
            assert [s.id_agendamento for s in u.snapshots.listar_por_data(DIA, DIA)] == [1]
            assert u.snapshots.obter_varios([]) == {}
            assert u.snapshots.apagar_anteriores_a(DIA) == 1
            u.commit()


class TestAgendas:
    def test_sync_preserva_inclusao_e_desativa_ausentes(self, uow: FabricaUoW) -> None:
        with uow() as u:
            assert u.agendas.sincronizar([agenda(10, "Clínico"), agenda(30, "Antiga")]) == (
                2,
                0,
                0,
            )
            u.agendas.definir_inclusao(10, False)
            u.agendas.definir_guiche(10, True)
            u.commit()
        with uow() as u:
            resultado = u.agendas.sincronizar(
                [agenda(10, "Clínico Geral", sala="Sala 01"), agenda(20, "Áudio")]
            )
            u.commit()
        assert resultado == (1, 1, 1)
        with uow() as u:
            agendas = {a.id_agenda: a for a in u.agendas.listar()}
        assert agendas[10].nome == "Clínico Geral"
        assert agendas[10].consultorio == "Sala 01"
        assert agendas[10].incluir_relatorio is False
        assert agendas[10].guiche is True  # o sync não desfaz a marcação do admin
        assert agendas[20].guiche is False
        assert agendas[30].ativa is False


class TestResumo:
    def test_reprocessar_nao_reseta_envio(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.resumos.salvar_metricas(DIA, {"kpis": {"atendimentos": 1}}, "1.0.0", hora("23:00"))
            assert u.resumos.reservar_envio(DIA)
            u.resumos.marcar_enviado(DIA, hora("07:59", DIA + timedelta(1)))
            u.resumos.salvar_metricas(DIA, {"kpis": {"atendimentos": 2}}, "1.0.1", hora("23:30"))
            u.commit()
        with uow() as u:
            r = u.resumos.obter(DIA)
        assert r is not None
        assert r.metricas["kpis"]["atendimentos"] == 2
        assert r.versao_regra == "1.0.1"
        assert r.status_envio is StatusEnvio.ENVIADO

    def test_reserva_de_envio_bloqueia_duplicidade(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.resumos.salvar_metricas(DIA, {}, "1.0.0", hora("23:00"))
            u.commit()
        with uow() as u:
            assert u.resumos.reservar_envio(DIA) is True
            assert u.resumos.reservar_envio(DIA) is False
            assert u.resumos.reservar_envio(DIA, forcar=True) is True
            u.resumos.marcar_falha_envio(DIA)
            assert u.resumos.reservar_envio(DIA) is True  # falha libera nova tentativa
            u.commit()
        with uow() as u:
            assert u.resumos.reservar_envio(date(2020, 1, 1)) is False

    def test_reservas_concorrentes_so_uma_vence(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.resumos.salvar_metricas(DIA, {}, "1.0.0", hora("23:00"))
            u.commit()
        resultados: list[bool] = []
        barreira = threading.Barrier(4)

        def reservar() -> None:
            with uow() as u:
                barreira.wait()
                resultados.append(u.resumos.reservar_envio(DIA))
                u.commit()

        threads = [threading.Thread(target=reservar) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(resultados) == [False, False, False, True]

    def test_listar_e_retencao(self, uow: FabricaUoW) -> None:
        with uow() as u:
            for dias in range(3):
                u.resumos.salvar_metricas(DIA - timedelta(dias), {}, "1.0.0", hora("23:00"))
            u.commit()
        with uow() as u:
            assert [r.data for r in u.resumos.listar_recentes(2)] == [DIA, DIA - timedelta(1)]
            assert u.resumos.apagar_anteriores_a(DIA - timedelta(1)) == 1
            u.commit()


class TestDestinatariosEConfiguracao:
    def test_cadastro_normaliza_e_reativa(self, uow: FabricaUoW) -> None:
        with uow() as u:
            d = u.destinatarios.adicionar(" Glauco@MultiLife.com.br ", "Glauco")
            u.destinatarios.definir_ativo(d.id, False)
            u.commit()
        with uow() as u:
            assert u.destinatarios.listar(apenas_ativos=True) == []
            reativado = u.destinatarios.adicionar("glauco@multilife.com.br", None)
            u.commit()
        assert reativado.id == d.id
        assert reativado.ativo
        assert reativado.nome == "Glauco"

    def test_configuracao_chave_valor(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.configuracoes.definir("turno_tarde_inicio", "12:30")
            u.configuracoes.definir("turno_tarde_inicio", "13:00")
            u.commit()
        with uow() as u:
            assert u.configuracoes.obter_todas() == {"turno_tarde_inicio": "13:00"}

    def test_cursor(self, uow: FabricaUoW) -> None:
        with uow() as u:
            assert u.cursores.obter("x") is None
            u.cursores.salvar("x", hora("08:00"))
            u.cursores.salvar("x", hora("08:01"))
            u.commit()
        with uow() as u:
            assert u.cursores.obter("x") == hora("08:01")


class TestExecucoes:
    def registrar(self, uow: FabricaUoW, job: str, quando: str, status: StatusJob, **d: str) -> int:
        with uow() as u:
            id_exec = u.execucoes.iniciar(job, hora(quando), {})
            u.execucoes.finalizar(id_exec, hora(quando), status, d)
            u.commit()
        return id_exec

    def test_consultas_de_auditoria(self, uow: FabricaUoW) -> None:
        self.registrar(uow, "coletar_ciclo", "08:00", StatusJob.SUCESSO)
        for minuto in range(1, 4):
            self.registrar(uow, "coletar_ciclo", f"08:0{minuto}", StatusJob.FALHA, erro="S000")
        self.registrar(uow, "consolidar_dia", "23:00", StatusJob.SUCESSO, referencia="2026-09-23")
        with uow() as u:
            assert u.execucoes.falhas_consecutivas("coletar_ciclo") == 3
            assert u.execucoes.houve_sucesso("consolidar_dia", "2026-09-23")
            assert not u.execucoes.houve_sucesso("consolidar_dia", "2026-09-22")
            assert u.execucoes.inicios_com_sucesso(
                "coletar_ciclo", hora("07:00"), hora("09:00")
            ) == [hora("08:00")]
            ultima = u.execucoes.ultima("coletar_ciclo")
            assert ultima is not None
            assert ultima.detalhe == {"erro": "S000"}
            assert ultima.duracao_ms == 0
            assert u.execucoes.ultima("coletar_ciclo", StatusJob.SUCESSO) is not None
            recentes = u.execucoes.recentes_por_job(por_job=2)
            assert [len(recentes[j]) for j in sorted(recentes)] == [2, 1]
            assert u.execucoes.apagar_anteriores_a(hora("08:02")) == 2  # 08:00 e 08:01
            u.commit()
        with uow() as u:
            u.execucoes.finalizar(9999, hora("09:00"), StatusJob.SUCESSO, {})  # inexistente: ignora

    def test_compactar_mantem_o_primeiro_sucesso_de_cada_minuto(self, uow: FabricaUoW) -> None:
        for segundos in ("00", "05", "10"):
            self.registrar(uow, "coletar_ciclo", f"08:00:{segundos}", StatusJob.SUCESSO)
            self.registrar(uow, "coletar_ciclo", f"08:01:{segundos}", StatusJob.SUCESSO)
        self.registrar(uow, "coletar_ciclo", "08:00:07", StatusJob.FALHA, erro="S000")
        self.registrar(uow, "coletar_ciclo", "08:01:30", StatusJob.SUCESSO)  # depois do limite
        self.registrar(uow, "consolidar_dia", "08:00:20", StatusJob.SUCESSO)  # outro job
        with uow() as u:
            assert u.execucoes.compactar_sucessos("coletar_ciclo", hora("08:01:20")) == 4
            u.commit()
        with uow() as u:
            restantes = u.execucoes.recentes_por_job(por_job=20)
        assert sorted(e.inicio for e in restantes["coletar_ciclo"]) == [
            hora("08:00:00"),
            hora("08:00:07"),
            hora("08:01:00"),
            hora("08:01:30"),
        ]
        assert len(restantes["consolidar_dia"]) == 1


def test_evento_gravado_preserva_origem(uow: FabricaUoW) -> None:
    e2 = replace(
        evento(5, Situacao.AGENDADO, Situacao.FALTOU, "19:00"),
        origem=OrigemEvento.VERIFICACAO_FALTA,
    )
    with uow() as u:
        u.eventos.inserir(e2)
        u.commit()
    with uow() as u:
        assert u.eventos.listar_por_agendamentos([5])[5][0].origem is OrigemEvento.VERIFICACAO_FALTA


class TestRelatorioFinanceiro:
    def test_resumo_e_destinatarios_separados_do_de_atendimentos(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.resumos_financeiros.salvar_metricas(
                DIA, {"tipo": "financeiro"}, "1.0.0", hora("05:45")
            )
            assert u.resumos_financeiros.reservar_envio(DIA)
            assert u.resumos_financeiros.reservar_envio(DIA) is False  # trava de duplicidade
            u.destinatarios_financeiro.adicionar("Diretoria@MultiLife.com.br", "Diretoria")
            u.destinatarios_financeiro.adicionar("diretoria@multilife.com.br", None)  # reativa
            u.commit()
        with uow() as u:
            assert u.resumos.obter(DIA) is None
            registro = u.resumos_financeiros.obter(DIA)
            assert registro is not None and registro.metricas == {"tipo": "financeiro"}
            financeiro = u.destinatarios_financeiro.listar()
            assert [(d.email, d.nome) for d in financeiro] == [
                ("diretoria@multilife.com.br", "Diretoria")
            ]
            assert "diretoria@multilife.com.br" not in {d.email for d in u.destinatarios.listar()}


class TestRelatorioSesmt:
    def test_resumo_e_destinatarios_separados_dos_demais(self, uow: FabricaUoW) -> None:
        with uow() as u:
            u.resumos_sesmt.salvar_metricas(DIA, {"tipo": "sesmt"}, "1.0.0", hora("02:00"))
            assert u.resumos_sesmt.reservar_envio(DIA)
            assert u.resumos_sesmt.reservar_envio(DIA) is False  # trava de duplicidade
            u.destinatarios_sesmt.adicionar("SESMT@MultiLife.com.br", "SESMT")
            u.destinatarios_sesmt.adicionar("sesmt@multilife.com.br", None)  # reativa
            u.commit()
        with uow() as u:
            assert u.resumos.obter(DIA) is None and u.resumos_financeiros.obter(DIA) is None
            registro = u.resumos_sesmt.obter(DIA)
            assert registro is not None and registro.metricas == {"tipo": "sesmt"}
            assert [(d.email, d.nome) for d in u.destinatarios_sesmt.listar()] == [
                ("sesmt@multilife.com.br", "SESMT")
            ]
            assert not u.destinatarios.listar() and not u.destinatarios_financeiro.listar()


class TestExamesPorMedico:
    def test_medicos_vistos_escolha_e_nome_atualizado(self, uow: FabricaUoW) -> None:
        ana, beto = Medico("34985-DF", "Ana"), Medico("31096-DF", "Beto")
        with uow() as u:
            u.medicos.registrar_vistos([ana, beto], DIA)
            u.medicos.registrar_vistos([replace(ana, nome="Ana Souza")], DIA - timedelta(days=3))
            u.medicos.definir_selecionado(beto.crm, True)
            u.medicos.adicionar(Medico("123-RS", "Caio"))  # cadastro manual já vem escolhido
            u.commit()
        with uow() as u:
            todos = {m.crm: m for m in u.medicos.listar()}
            escolhidos = [m.crm for m in u.medicos.selecionados()]
        assert todos[ana.crm].nome == "Ana Souza"
        assert todos[ana.crm].visto_em == DIA  # dia mais antigo não volta a data
        assert not todos[ana.crm].selecionado
        assert escolhidos == ["31096-DF", "123-RS"]  # pelo nome: Beto, Caio
        assert todos["123-RS"].visto_em is None

    def test_exames_do_dia_sem_nome_coleta_e_retencao(self, uow: FabricaUoW) -> None:
        def exame(id_: int, dia: date, empresa: str) -> ExameClinico:
            return ExameClinico(
                id_, dia, 10, 77, "34985-DF", "Ana", "Periódico", empresa, funcionario="Fulano"
            )

        ontem = DIA - timedelta(days=1)
        with uow() as u:
            u.exames.substituir_dia(ontem, [exame(1, ontem, "Nome antigo")])
            u.exames.substituir_dia(DIA, [exame(2, DIA, "Conplan"), exame(3, DIA, "Conplan")])
            u.exames.substituir_dia(DIA, [exame(2, DIA, "Conplan")])  # reprocessamento
            u.exames.registrar_coleta(ColetaExames(DIA, hora("23:20"), 5, 1, ("34985-DF",)))
            u.exames.registrar_coleta(ColetaExames(DIA, hora("23:30"), 6, 1, ("34985-DF",)))
            u.commit()
        with uow() as u:
            do_dia = u.exames.do_dia(DIA)
            nomes = u.exames.nomes_empresas({10, 99})
            coleta = u.exames.coleta(DIA)
            recentes = u.exames.coletas_recentes()
        assert [e.id for e in do_dia] == [2]
        assert do_dia[0].funcionario == ""  # nunca vai para o banco
        assert nomes == {10: "Conplan"}  # o nome mais recente da empresa
        assert coleta is not None and (coleta.clinicos, coleta.medicos) == (6, ("34985-DF",))
        assert [c.data for c in recentes] == [DIA]
        with uow() as u:
            assert u.exames.apagar_anteriores_a(DIA) == 1
            u.commit()
        with uow() as u:
            assert u.exames.do_dia(ontem) == []
