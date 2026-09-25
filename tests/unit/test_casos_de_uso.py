"""Casos de uso da camada de aplicação, com fakes em memória."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from relatorio.application.coleta import (
    CURSOR_AGENDAMENTOS,
    ResolvedorAgendas,
)
from relatorio.application.configuracao import (
    CHAVE_EMAIL_TECNICO,
    CHAVE_TARDE_INICIO,
    CHAVE_UNIDADES,
    ConfiguracaoRelatorio,
    JanelaColeta,
)
from relatorio.application.consolidacao import JOB_CONSOLIDAR, dia_para_consolidar
from relatorio.application.envio import RelatorioIndisponivel, dia_do_relatorio
from relatorio.application.manutencao import subtrair_meses
from relatorio.application.metricas import JOB_COLETA
from relatorio.application.modelos import StatusEnvio, StatusJob
from relatorio.domain.entidades import OrigemEvento, Situacao
from tests.fabricas import DIA, agenda, hora, registro_sgg
from tests.unit.conftest import Sistema

AGUARDANDO, EM_ATEND, ATENDIDO = Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO


def eventos_de(sistema: Sistema, id_agendamento: int) -> list[tuple[Situacao | None, Situacao]]:
    return [
        (e.status_anterior, e.status_novo)
        for e in sorted(sistema.banco.eventos, key=lambda e: e.ocorrido_em)
        if e.id_agendamento == id_agendamento
    ]


def simular_atendimento_completo(sistema: Sistema) -> None:
    """Três ciclos de polling: chegada, chamada e fim de um atendimento."""
    sistema.banco.agendas[10] = agenda(10, "Clínico", sala="Sala 01")
    ciclos = [
        [registro_sgg(1, AGUARDANDO, "07:55")],
        [registro_sgg(1, EM_ATEND, "08:05")],
        [registro_sgg(1, ATENDIDO, "08:20")],
    ]
    sistema.relogio.instante = hora("07:56")
    for ciclo in ciclos:
        sistema.sgg.editados.append(ciclo)
    for instante in ("07:56", "08:06", "08:21"):
        sistema.relogio.instante = hora(instante)
        sistema.coletar.executar()


class TestColetarCiclo:
    def test_primeiro_ciclo_comeca_no_inicio_do_dia_e_grava_cursor(self, sistema: Sistema) -> None:
        sistema.sgg.editados.append([registro_sgg(1, AGUARDANDO, "07:59")])
        resultado = sistema.coletar.executar()
        nome, de, ate = sistema.sgg.chamadas[0]
        assert (nome, de, ate) == ("editados", hora("00:00"), hora("08:00"))
        assert resultado["eventos_novos"] == 1
        assert resultado["requisicoes"] == 1
        assert sistema.banco.cursores[CURSOR_AGENDAMENTOS] == hora("08:00")

    def test_ciclo_seguinte_usa_sobreposicao_de_2_minutos(self, sistema: Sistema) -> None:
        sistema.banco.cursores[CURSOR_AGENDAMENTOS] = hora("08:00")
        sistema.relogio.instante = hora("08:01")
        sistema.coletar.executar()
        _, de, ate = sistema.sgg.chamadas[0]
        assert (de, ate) == (hora("07:58"), hora("08:01"))

    def test_cursor_muito_antigo_e_limitado(self, sistema: Sistema) -> None:
        sistema.banco.cursores[CURSOR_AGENDAMENTOS] = hora("08:00") - timedelta(days=30)
        sistema.coletar.executar()
        _, de, _ = sistema.sgg.chamadas[0]
        assert de == hora("08:00") - timedelta(days=7)

    def test_registro_repetido_na_sobreposicao_nao_duplica(self, sistema: Sistema) -> None:
        registro = registro_sgg(1, AGUARDANDO, "07:59")
        sistema.sgg.editados.extend([[registro], [registro]])
        sistema.coletar.executar()
        sistema.relogio.avancar(minutes=1)
        resultado = sistema.coletar.executar()
        assert resultado["eventos_novos"] == 0
        assert len(sistema.banco.eventos) == 1

    def test_falha_na_api_nao_avanca_cursor(self, sistema: Sistema) -> None:
        sistema.banco.cursores[CURSOR_AGENDAMENTOS] = hora("07:59")
        sistema.sgg.falhar = True
        with pytest.raises(Exception, match="indisponível"):
            sistema.coletar.executar()
        assert sistema.banco.cursores[CURSOR_AGENDAMENTOS] == hora("07:59")

    def test_fluxo_completo_gera_transicoes(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        assert eventos_de(sistema, 1) == [
            (None, AGUARDANDO),
            (AGUARDANDO, EM_ATEND),
            (EM_ATEND, ATENDIDO),
        ]
        assert sistema.banco.snapshots[1].id_agenda == 10

    def test_duas_edicoes_do_mesmo_agendamento_no_lote(self, sistema: Sistema) -> None:
        sistema.sgg.editados.append(
            [registro_sgg(1, EM_ATEND, "07:58"), registro_sgg(1, AGUARDANDO, "07:50")]
        )
        resultado = sistema.coletar.executar()
        assert resultado["eventos_novos"] == 2
        assert eventos_de(sistema, 1) == [(None, AGUARDANDO), (AGUARDANDO, EM_ATEND)]

    def test_salto_e_contado(self, sistema: Sistema) -> None:
        sistema.sgg.editados.extend(
            [[registro_sgg(1, AGUARDANDO, "07:50")], [registro_sgg(1, ATENDIDO, "08:20")]]
        )
        sistema.coletar.executar()
        sistema.relogio.instante = hora("08:21")
        assert sistema.coletar.executar()["saltos"] == 1


class TestResolvedorAgendas:
    def test_por_nome_e_unidade(self) -> None:
        r = ResolvedorAgendas(
            [agenda(1, "Clínico", unidade=1), agenda(2, "Clínico", unidade=2), agenda(3, "Áudio")]
        )
        assert r.resolver("clinico", 2) == 2
        assert r.resolver("  CLÍNICO ", 1) == 1
        assert r.resolver("Clínico", None) is None  # ambíguo entre unidades
        assert r.resolver("audio", 99) == 3
        assert r.resolver("Inexistente", 1) is None


class TestReconciliacaoESync:
    def test_reconciliacao_recupera_transicao_perdida(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.sgg.do_dia = [
            registro_sgg(1, ATENDIDO, "08:20"),
            registro_sgg(2, Situacao.FALTOU, "10:00", hora_agendada="09:00"),
        ]
        sistema.relogio.instante = hora("18:30")
        resultado = sistema.reconciliar.executar(DIA)
        assert resultado["eventos_novos"] == 1
        assert resultado["referencia"] == "2026-09-23"
        evento = next(e for e in sistema.banco.eventos if e.id_agendamento == 2)
        assert evento.origem is OrigemEvento.RECONCILIACAO

    def test_sync_preserva_inclusao_e_desativa_ausentes(self, sistema: Sistema) -> None:
        sistema.banco.agendas[10] = agenda(10, "Clínico", incluir=False)
        sistema.banco.agendas[30] = agenda(30, "Antiga")
        sistema.sgg.lista_agendas = [agenda(10, "Clínico", sala="Sala 01"), agenda(20, "Áudio")]
        resultado = sistema.sincronizar.executar()
        assert resultado == {"agendas": 2, "novas": 1, "atualizadas": 1, "desativadas": 1}
        assert sistema.banco.agendas[10].sala == "Sala 01"
        assert not sistema.banco.agendas[10].incluir_relatorio
        assert not sistema.banco.agendas[30].ativa

    def test_sync_com_resposta_vazia_nao_mexe_no_cadastro(self, sistema: Sistema) -> None:
        sistema.banco.agendas[10] = agenda(10, "Clínico")
        assert sistema.sincronizar.executar()["agendas"] == 0
        assert sistema.banco.agendas[10].ativa


def registrar_coletas(sistema: Sistema, inicios: list[datetime]) -> None:
    uow = sistema.uow()
    for instante in inicios:
        id_exec = uow.execucoes.iniciar(JOB_COLETA, instante, {})
        uow.execucoes.finalizar(id_exec, instante, StatusJob.SUCESSO, {})


class TestObterMetricas:
    def test_respeita_unidades_selecionadas(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.banco.configuracoes[CHAVE_UNIDADES] = "2"
        sistema.relogio.instante = hora("23:00")
        resultado = sistema.metricas.executar(hora("00:00"), hora("23:59:59"))
        assert resultado.metricas.kpis.atendimentos == 0
        assert resultado.unidades == ("Unidade 2",)

    def test_falhas_de_coleta_vem_das_execucoes(self, sistema: Sistema) -> None:
        minutos = [hora("06:00") + timedelta(minutes=m) for m in range(0, 12 * 60 + 1)]
        registrar_coletas(sistema, [m for m in minutos if not hora("10:12") < m < hora("10:40")])
        sistema.relogio.instante = hora("23:00")
        resultado = sistema.metricas.executar(hora("00:00"), hora("23:59:59"))
        falhas = resultado.metricas.alertas.falhas_coleta
        assert [(f.inicio, f.fim) for f in falhas] == [(hora("10:13"), hora("10:40"))]

    def test_tempo_real_so_olha_ate_agora(self, sistema: Sistema) -> None:
        registrar_coletas(sistema, [hora("06:00") + timedelta(minutes=m) for m in range(0, 121)])
        sistema.relogio.instante = hora("08:00")
        resultado = sistema.metricas.executar(hora("00:00"), hora("08:00"))
        assert resultado.metricas.alertas.falhas_coleta == ()

    def test_coletor_do_painel_desliga_alerta_de_coleta(self) -> None:
        sistema = Sistema(coletor_habilitado=False)
        sistema.relogio.instante = hora("23:00")
        resultado = sistema.metricas.executar(hora("00:00"), hora("23:59:59"))
        assert resultado.metricas.alertas.falhas_coleta == ()


class TestConsolidarDia:
    def test_grava_resumo_com_comparativo(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.relogio.instante = hora("23:00")
        sistema.consolidar.executar(DIA - timedelta(days=7))  # base vazia (sem movimento)
        resultado = sistema.consolidar.executar(DIA)
        assert resultado["atendimentos"] == 1
        registro = sistema.banco.resumos[DIA]
        assert registro.status_envio is StatusEnvio.PENDENTE
        assert registro.versao_regra == "1.1.0"
        m = registro.metricas
        assert m["kpis"]["tma_s"] == 15 * 60
        assert m["kpis"]["espera_media_s"] == 10 * 60
        assert m["unidades"] == ["Todas as unidades"]
        assert m["comparativo"]["disponivel"] is False  # semana anterior sem movimento

    def test_comparativo_com_semana_anterior(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.banco.resumos.clear()
        base = DIA - timedelta(days=7)
        uow = sistema.uow()
        uow.resumos.salvar_metricas(
            base, {"kpis": {"atendimentos": 2, "agendados": 2}}, "1.0.0", hora("23:00", base)
        )
        sistema.relogio.instante = hora("23:00")
        sistema.consolidar.executar(DIA)
        item = next(
            i
            for i in sistema.banco.resumos[DIA].metricas["comparativo"]["itens"]
            if i["chave"] == "atendimentos"
        )
        assert item["variacao"] == pytest.approx(-0.5)
        assert item["avaliacao"] == "negativa"

    def test_falta_na_agenda_do_consultorio_conta_como_falta(self, sistema: Sistema) -> None:
        sistema.sgg.editados.append([registro_sgg(5, Situacao.AGENDADO, "07:00")])
        sistema.coletar.executar()
        sistema.sgg.do_dia = [registro_sgg(5, Situacao.FALTOU, "19:00")]
        sistema.relogio.instante = hora("23:00")
        resultado = sistema.consolidar.executar(DIA)
        assert resultado["faltas"] == 1
        assert resultado["faltas_confirmadas_na_agenda"] == 1
        m = sistema.banco.resumos[DIA].metricas
        assert m["kpis"]["sem_baixa"] == 0
        assert eventos_de(sistema, 5)[-1] == (Situacao.AGENDADO, Situacao.FALTOU)

    def test_finalizacao_depois_das_18_30_entra_na_conferencia_final(
        self, sistema: Sistema
    ) -> None:
        sistema.sgg.editados.append(
            [registro_sgg(6, AGUARDANDO, "17:50"), registro_sgg(6, EM_ATEND, "17:58")]
        )
        sistema.relogio.instante = hora("18:00")
        sistema.coletar.executar()
        sistema.sgg.do_dia = [registro_sgg(6, ATENDIDO, "18:40")]
        sistema.relogio.instante = hora("23:00")
        resultado = sistema.consolidar.executar(DIA)
        assert resultado["atendimentos"] == 1
        m = sistema.banco.resumos[DIA].metricas
        assert m["kpis"]["tma_s"] == 42 * 60
        assert m["alertas"]["em_atendimento_aberto"]["total"] == 0
        assert m["alertas"]["faltas_confirmadas_na_agenda"]["total"] == 0

    def test_sgg_fora_do_ar_nao_impede_consolidacao(self, sistema: Sistema) -> None:
        sistema.sgg.editados.append([registro_sgg(5, Situacao.AGENDADO, "07:00")])
        sistema.coletar.executar()
        sistema.sgg.falhar = True
        sistema.relogio.instante = hora("23:00")
        resultado = sistema.consolidar.executar(DIA)
        assert resultado["verificacao_faltas_indisponivel"] is True
        m = sistema.banco.resumos[DIA].metricas
        assert m["kpis"]["sem_baixa"] == 1
        assert m["alertas"]["verificacao_faltas_indisponivel"] is True

    def test_sem_candidatos_nao_consulta_sgg(self, sistema: Sistema) -> None:
        sistema.relogio.instante = hora("23:00")
        sistema.consolidar.executar(DIA)
        assert sistema.sgg.chamadas == []
        assert sistema.banco.resumos[DIA].metricas["sem_movimento"] is True

    def test_retentativa_pula_dia_ja_consolidado(self, sistema: Sistema) -> None:
        uow = sistema.uow()
        id_exec = uow.execucoes.iniciar(JOB_CONSOLIDAR, hora("23:00"), {})
        uow.execucoes.finalizar(
            id_exec, hora("23:01"), StatusJob.SUCESSO, {"referencia": DIA.isoformat()}
        )
        resultado = sistema.consolidar.executar_agendado(DIA, tentativa=2)
        assert resultado["status"] == "ja_consolidado"

    def test_ultima_tentativa_com_falha_alerta_tecnico(self, sistema: Sistema) -> None:
        sistema.banco.configuracoes[CHAVE_TARDE_INICIO] = "05:00"  # config inválida → erro
        with pytest.raises(ValueError):
            sistema.consolidar.executar_agendado(DIA, tentativa=2)
        assert sistema.alertas_enviados() == []
        with pytest.raises(ValueError):
            sistema.consolidar.executar_agendado(DIA, tentativa=3)
        assert sistema.alertas_enviados() == ["[Alerta] Consolidação do dia falhou"]

    @pytest.mark.parametrize(
        ("agora", "esperado"),
        [("23:00", DIA), ("23:59", DIA)],
    )
    def test_dia_para_consolidar_as_23(self, agora: str, esperado: date) -> None:
        assert dia_para_consolidar(hora(agora)) == esperado

    def test_dia_para_consolidar_nas_retentativas(self) -> None:
        amanha = DIA + timedelta(days=1)
        assert dia_para_consolidar(hora("02:00", amanha)) == DIA
        assert dia_para_consolidar(hora("05:00", amanha)) == DIA


def preparar_envio(sistema: Sistema) -> None:
    simular_atendimento_completo(sistema)
    uow = sistema.uow()
    uow.destinatarios.adicionar("glauco@multilife.com.br", "Glauco")
    uow.destinatarios.adicionar("tecnologia@multilife.com.br", "Tecnologia")
    sistema.relogio.instante = hora("23:00")
    sistema.consolidar.executar(DIA)
    sistema.relogio.instante = hora("07:59", DIA + timedelta(days=1))


class TestEnviarRelatorio:
    def test_envia_para_destinatarios_ativos(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.uow().destinatarios.definir_ativo(2, False)
        resultado = sistema.enviar.executar(DIA)
        assert resultado["status"] == "enviado"
        destinatarios, conteudo = sistema.email.enviados[0]
        assert destinatarios == ["glauco@multilife.com.br"]
        assert conteudo.assunto == "Resumo de Atendimentos — 23/09/2026 (quarta-feira)"
        registro = sistema.banco.resumos[DIA]
        assert registro.status_envio is StatusEnvio.ENVIADO
        assert registro.enviado_em == hora("07:59", DIA + timedelta(days=1))

    def test_reexecutar_nao_duplica_email(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.enviar.executar(DIA)
        assert sistema.enviar.executar(DIA)["status"] == "ja_enviado"
        assert len(sistema.email.enviados) == 1

    def test_reenvio_manual_forcado(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.enviar.executar(DIA)
        assert sistema.enviar.executar(DIA, forcar=True)["status"] == "enviado"
        assert len(sistema.email.enviados) == 2

    def test_falha_smtp_marca_falha_e_permite_nova_tentativa(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.email.falhar = True
        with pytest.raises(ConnectionError):
            sistema.enviar.executar_agendado(DIA, tentativa=1)
        assert sistema.banco.resumos[DIA].status_envio is StatusEnvio.FALHA
        assert sistema.alertas_enviados() == []
        sistema.email.falhar = False
        assert sistema.enviar.executar_agendado(DIA, tentativa=2)["status"] == "enviado"

    def test_terceira_falha_alerta_tecnico(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.email.falhar = True
        for tentativa in (1, 2, 3):
            with pytest.raises(ConnectionError):
                sistema.enviar.executar_agendado(DIA, tentativa=tentativa)
        # o alerta também falha (SMTP fora), mas nunca derruba o fluxo
        assert sistema.banco.resumos[DIA].status_envio is StatusEnvio.FALHA

    def test_sem_resumo_consolida_na_hora(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.uow().destinatarios.adicionar("glauco@multilife.com.br", None)
        sistema.relogio.instante = hora("07:59", DIA + timedelta(days=1))
        assert sistema.enviar.executar(DIA)["status"] == "enviado"
        assert DIA in sistema.banco.resumos

    def test_consolidacao_de_ultima_hora_falhou_alerta_e_nao_envia(self, sistema: Sistema) -> None:
        sistema.banco.configuracoes[CHAVE_TARDE_INICIO] = "05:00"
        sistema.banco.configuracoes[CHAVE_EMAIL_TECNICO] = "ti@multilife.com.br"
        with pytest.raises(RelatorioIndisponivel):
            sistema.enviar.executar_agendado(DIA, tentativa=1)
        assert sistema.alertas_enviados() == ["[Alerta] Relatório não enviado"]
        assert sistema.email.enviados[0][0] == ["ti@multilife.com.br"]

    def test_sem_destinatarios_alerta(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.relogio.instante = hora("23:00")
        sistema.consolidar.executar(DIA)
        assert sistema.enviar.executar(DIA)["status"] == "sem_destinatarios"
        assert sistema.alertas_enviados() == ["[Alerta] Relatório sem destinatários"]

    def test_override_de_destinatarios_para_staging(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.enviar._override = ("ti@multilife.com.br",)
        sistema.enviar.executar(DIA)
        assert sistema.email.enviados[0][0] == ["ti@multilife.com.br"]

    def test_dia_do_relatorio_e_ontem(self) -> None:
        segunda = date(2026, 9, 28)
        assert dia_do_relatorio(hora("07:59", segunda)) == date(2026, 9, 27)  # domingo


class TestVerificarEnvio:
    def test_estados(self, sistema: Sistema) -> None:
        assert sistema.verificar_envio.executar(DIA)["status"] == "alertado"
        preparar_envio(sistema)
        sistema.uow().resumos.marcar_falha_envio(DIA)
        assert sistema.verificar_envio.executar(DIA)["status"] == "falha_ja_alertada"
        sistema.enviar.executar(DIA)
        assert sistema.verificar_envio.executar(DIA)["status"] == "ok"


class TestManutencao:
    def test_reprocessar_reconcilia_consolida_e_reenvia(self, sistema: Sistema) -> None:
        preparar_envio(sistema)
        sistema.enviar.executar(DIA)
        sistema.sgg.do_dia = [registro_sgg(1, ATENDIDO, "08:20")]
        detalhe = sistema.reprocessar.executar(DIA, reconciliar=True, enviar=True)
        assert detalhe["envio"] == {
            "referencia": "2026-09-23",
            "status": "enviado",
            "destinatarios": 2,
            "sem_movimento": False,
        }
        assert len(sistema.email.enviados) == 2

    def test_reprocessar_sem_sgg_segue_com_o_que_tem(self, sistema: Sistema) -> None:
        sistema.sgg.falhar = True
        detalhe = sistema.reprocessar.executar(DIA, reconciliar=True)
        assert str(detalhe["reconciliacao"]).startswith("indisponível")
        assert DIA in sistema.banco.resumos

    def test_limpar_retencao_24_meses(self, sistema: Sistema) -> None:
        simular_atendimento_completo(sistema)
        sistema.relogio.instante = hora("03:00", date(2028, 10, 1))
        resultado = sistema.limpar.executar()
        assert resultado["limite"] == "2026-10-01"
        assert resultado["eventos"] == 3
        assert resultado["snapshots"] == 1
        assert sistema.banco.eventos == []

    @pytest.mark.parametrize(
        ("dia", "meses", "esperado"),
        [
            (date(2026, 9, 23), 24, date(2024, 9, 23)),
            (date(2026, 3, 31), 1, date(2026, 2, 28)),
            (date(2028, 2, 29), 24, date(2026, 2, 28)),
            (date(2026, 1, 15), 13, date(2024, 12, 15)),
        ],
    )
    def test_subtrair_meses(self, dia: date, meses: int, esperado: date) -> None:
        assert subtrair_meses(dia, meses) == esperado

    def test_alerta_na_decima_falha_seguida_de_coleta(self, sistema: Sistema) -> None:
        uow = sistema.uow()
        for n in range(11):
            id_exec = uow.execucoes.iniciar(JOB_COLETA, hora("09:00") + timedelta(minutes=n), {})
            uow.execucoes.finalizar(id_exec, hora("09:00"), StatusJob.FALHA, {"erro": "S000"})
            enviado = sistema.alerta_coleta.verificar()
            assert enviado is (n == 9)
        assert sistema.alertas_enviados() == ["[Alerta] Coleta do SGG falhando"]


class TestConfiguracao:
    def test_mesclar_e_serializar(self) -> None:
        config = ConfiguracaoRelatorio().mesclar(
            {
                "turno_tarde_inicio": "12:30",
                "atipico_max_minutos": "120",
                "unidades_incluidas": "3, 1",
                "email_alerta_tecnico": " ",
            }
        )
        assert config.turnos.tarde_inicio.isoformat() == "12:30:00"
        assert config.regras.atipico_max == timedelta(minutes=120)
        assert config.unidades == frozenset({1, 3})
        assert config.email_alerta_tecnico == "tecnologia@multilife.com.br"
        valores = config.para_valores()
        assert valores["unidades_incluidas"] == "1,3"
        assert ConfiguracaoRelatorio().mesclar(valores) == config

    def test_janela_de_coleta(self) -> None:
        janela = JanelaColeta()
        assert janela.contem(hora("06:00"))
        assert janela.contem(hora("18:00"))
        assert not janela.contem(hora("18:01"))
        assert janela.do_dia(DIA) == (hora("06:00"), hora("18:00"))
