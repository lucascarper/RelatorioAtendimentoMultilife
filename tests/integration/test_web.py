"""Admin web e /health contra o PostgreSQL de teste."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from relatorio.application.modelos import StatusJob
from relatorio.application.ports import FabricaUoW
from relatorio.config import Settings
from relatorio.domain.comparativo import comparar
from relatorio.domain.entidades import Situacao, TipoAgenda
from relatorio.domain.metricas import RegrasMetricas, calcular_metricas
from relatorio.domain.resumo import ResumoDiario
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.app import create_app
from relatorio.interfaces.web.seguranca import gerar_hash_senha
from tests.fabricas import DIA, agenda, atendimento, evento, hora, snapshot
from tests.fakes import RelogioFixo
from tests.integration.conftest import _url

pytestmark = pytest.mark.integration

RAIZ = Path(__file__).resolve().parents[2]
SENHA = "senha-bem-forte-123"
HASH = gerar_hash_senha(SENHA)


@pytest.fixture
def relogio() -> RelogioFixo:
    return RelogioFixo(hora("10:00", DIA + timedelta(days=1)))


@pytest.fixture
def container(
    uow: FabricaUoW, engine: Engine, tmp_path: Path, relogio: RelogioFixo
) -> Iterator[Container]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=_url() or "",
        admin_user="admin",
        admin_password_hash=HASH,  # type: ignore[arg-type]
        secret_key="x" * 40,  # type: ignore[arg-type]
        email_backend="arquivo",
        email_saida_dir=tmp_path / "emails",
        templates_dir=RAIZ / "templates",
        static_dir=RAIZ / "static",
    )
    c = Container(settings)
    c.__dict__["relogio"] = relogio  # relógio controlado antes de montar os casos de uso
    yield c
    c.fechar()


@pytest.fixture
def cliente(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container.settings, container)) as c:
        yield c


def csrf(cliente: TestClient, url: str = "/login") -> str:
    pagina = cliente.get(url).text
    achado = re.search(r'name="csrf" value="([^"]+)"', pagina)
    assert achado, "página sem token CSRF"
    return achado.group(1)


def entrar(cliente: TestClient) -> str:
    token = csrf(cliente)
    resposta = cliente.post(
        "/login",
        data={"usuario": "admin", "senha": SENHA, "csrf": token},
        follow_redirects=False,
    )
    assert resposta.status_code == 303
    return csrf(cliente, "/admin")


class TestHealth:
    def test_liveness(self, cliente: TestClient) -> None:
        assert cliente.get("/health/live").json() == {"status": "ok"}

    def test_fora_do_expediente_ok(self, cliente: TestClient, relogio: RelogioFixo) -> None:
        relogio.instante = hora("23:00")
        resposta = cliente.get("/health")
        assert resposta.status_code == 200
        assert resposta.json()["em_horario_de_coleta"] is False

    def test_coleta_atrasada_503(
        self, cliente: TestClient, uow: FabricaUoW, relogio: RelogioFixo
    ) -> None:
        with uow() as u:
            id_exec = u.execucoes.iniciar(
                "coletar_ciclo", relogio.instante - timedelta(minutes=9), {}
            )
            u.execucoes.finalizar(id_exec, relogio.instante, StatusJob.SUCESSO, {})
            u.commit()
        resposta = cliente.get("/health")
        assert resposta.status_code == 503
        assert resposta.json()["status"] == "atrasado"
        with uow() as u:
            id_exec = u.execucoes.iniciar(
                "coletar_ciclo", relogio.instante - timedelta(minutes=1), {}
            )
            u.execucoes.finalizar(id_exec, relogio.instante, StatusJob.SUCESSO, {})
            u.commit()
        assert cliente.get("/health").status_code == 200

    def test_logo_apos_abrir_a_janela_nao_acusa_atraso(
        self, cliente: TestClient, relogio: RelogioFixo
    ) -> None:
        relogio.instante = hora("06:03")
        assert cliente.get("/health").status_code == 200


class TestLogin:
    def test_admin_exige_login(self, cliente: TestClient) -> None:
        resposta = cliente.get("/admin", follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"] == "/login"
        htmx = cliente.post("/admin/agendas/1/incluir", headers={"HX-Request": "true"})
        assert htmx.status_code == 401
        assert htmx.headers["HX-Redirect"] == "/login"

    def test_sem_csrf_recusa(self, cliente: TestClient) -> None:
        resposta = cliente.post("/login", data={"usuario": "admin", "senha": SENHA})
        assert resposta.status_code == 403

    def test_senha_errada_e_bloqueio_apos_5_tentativas(self, cliente: TestClient) -> None:
        token = csrf(cliente)
        for tentativa in range(5):
            r = cliente.post("/login", data={"usuario": "admin", "senha": "x", "csrf": token})
            assert r.status_code == 401, tentativa
        assert "Restam 0 tentativa(s)" in r.text
        bloqueado = cliente.post("/login", data={"usuario": "admin", "senha": SENHA, "csrf": token})
        assert bloqueado.status_code == 429
        assert "Aguarde 15 minutos" in bloqueado.text

    def test_login_e_logout(self, cliente: TestClient) -> None:
        token = entrar(cliente)
        assert cliente.get("/admin").status_code == 200
        cliente.post("/logout", data={"csrf": token})
        assert cliente.get("/admin", follow_redirects=False).status_code == 303

    def test_cabecalhos_de_seguranca(self, cliente: TestClient) -> None:
        resposta = cliente.get("/login")
        assert resposta.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert "script-src 'self'" in resposta.headers["Content-Security-Policy"]
        assert resposta.headers["X-Content-Type-Options"] == "nosniff"


class TestPaginas:
    @pytest.mark.parametrize(
        ("url", "texto"),
        [
            ("/admin", "Gestão do relatório"),
            ("/admin/atendimento/gestao", "Reprocessar ou reenviar uma data"),
            ("/admin/destinatarios", "Cadastrar"),
            ("/admin/agendas", "Agendas (salas de exames, consultórios e guichês)"),
            ("/admin/configuracoes", "Início da tarde (corte)"),
            ("/admin/execucoes", "Execuções dos jobs"),
        ],
    )
    def test_paginas_renderizam(self, cliente: TestClient, url: str, texto: str) -> None:
        entrar(cliente)
        resposta = cliente.get(url)
        assert resposta.status_code == 200
        assert texto in resposta.text
        assert "/static/brand/logo-multilife-escuro.png" in resposta.text


class TestDestinatarios:
    def test_cadastro_validacao_e_alternancia_htmx(
        self, cliente: TestClient, uow: FabricaUoW
    ) -> None:
        token = entrar(cliente)
        invalido = cliente.post("/admin/destinatarios", data={"email": "x", "csrf": token})
        assert "Informe um e-mail válido." in invalido.text
        ok = cliente.post(
            "/admin/destinatarios",
            data={"email": "Glauco@MultiLife.com.br", "nome": "Glauco", "csrf": token},
        )
        assert "glauco@multilife.com.br cadastrado e ativo." in ok.text
        with uow() as u:
            [d] = u.destinatarios.listar()
        linha = cliente.post(
            f"/admin/destinatarios/{d.id}/ativo",
            data={"ativo": "false"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
        assert linha.status_code == 200
        assert 'aria-checked="false"' in linha.text
        assert linha.text.strip().startswith(f'<tr id="destinatario-{d.id}"')
        with uow() as u:
            assert u.destinatarios.listar(apenas_ativos=True) == []


class TestConfiguracoesEUnidades:
    def test_salvar_configuracoes(self, cliente: TestClient, uow: FabricaUoW) -> None:
        token = entrar(cliente)
        dados = {
            "manha_inicio": "06:00",
            "tarde_inicio": "12:30",
            "tarde_fim": "18:00",
            "atipico_min": "2",
            "atipico_max": "150",
            "email_tecnico": "ti@multilife.com.br",
            "csrf": token,
        }
        resposta = cliente.post("/admin/configuracoes", data=dados)
        assert "Configurações salvas" in resposta.text
        with uow() as u:
            valores = u.configuracoes.obter_todas()
        assert valores["turno_tarde_inicio"] == "12:30"
        assert valores["atipico_max_minutos"] == "150"
        assert valores["email_alerta_tecnico"] == "ti@multilife.com.br"

        invalido = cliente.post("/admin/configuracoes", data={**dados, "tarde_inicio": "05:00"})
        assert "Turnos inválidos" in invalido.text

    def test_unidades_e_agendas(self, cliente: TestClient, uow: FabricaUoW) -> None:
        with uow() as u:
            u.agendas.sincronizar([agenda(10, "Clínico", sala="Sala 01", unidade=7)])
            u.commit()
        token = entrar(cliente)
        pagina = cliente.get("/admin/agendas").text
        assert "Unidade 7" in pagina
        vazio = cliente.post("/admin/unidades", data={"modo": "selecionadas", "csrf": token})
        assert "Selecione ao menos uma unidade" in vazio.text
        cliente.post(
            "/admin/unidades", data={"modo": "selecionadas", "unidades": "7", "csrf": token}
        )
        with uow() as u:
            assert u.configuracoes.obter_todas()["unidades_incluidas"] == "7"
        linha = cliente.post(
            "/admin/agendas/10/incluir", data={"incluir": "false"}, headers={"X-CSRF-Token": token}
        )
        assert "Fora" in linha.text
        assert 'value="consultorio" selected' in pagina
        sala = cliente.post(
            "/admin/agendas/10/tipo", data={"tipo": "sala_exames"}, headers={"X-CSRF-Token": token}
        )
        assert 'value="sala_exames" selected' in sala.text and "Sala de exames" in sala.text
        with uow() as u:
            tipo = next(a for a in u.agendas.listar() if a.id_agenda == 10).tipo
            assert tipo is TipoAgenda.SALA_EXAMES
        invalido = cliente.post(
            "/admin/agendas/10/tipo", data={"tipo": "sala"}, headers={"X-CSRF-Token": token}
        )
        assert invalido.status_code == 422
        sem_chave = cliente.post("/admin/agendas/sincronizar", data={"csrf": token})
        assert "SGG indisponível" in sem_chave.text


class TestReprocessamento:
    def test_reprocessar_reenviar_e_ver(
        self, cliente: TestClient, uow: FabricaUoW, tmp_path: Path
    ) -> None:
        with uow() as u:
            u.destinatarios.adicionar("glauco@multilife.com.br", None)
            for e in (
                evento(1, None, Situacao.AGUARDANDO, "08:00"),
                evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:10"),
                evento(1, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "08:25"),
            ):
                u.eventos.inserir(e)
            u.snapshots.salvar(snapshot(1, Situacao.ATENDIDO))
            u.commit()
        token = entrar(cliente)
        resposta = cliente.post(
            "/admin/relatorios/reprocessar",
            data={"data": DIA.isoformat(), "enviar": "true", "reconciliar": "true", "csrf": token},
        )
        # Responde na hora; o trabalho roda depois da resposta (o TestClient espera).
        assert "Reprocessamento de 23/09/2026 iniciado." in resposta.text
        assert len(list((tmp_path / "emails").glob("*.eml"))) == 1
        with uow() as u:
            [execucao] = u.execucoes.recentes_por_job(1)["reprocessar"]
        assert execucao.status is StatusJob.SUCESSO
        assert execucao.detalhe["envio"] == "enviado"
        assert execucao.detalhe["destinatarios"] == 1
        assert execucao.detalhe["reconciliacao"].startswith("SGG indisponível")
        assert "Reprocessamento pelo admin" in cliente.get("/admin/execucoes").text

        previa = cliente.get(f"/admin/relatorios/{DIA.isoformat()}")
        assert previa.status_code == 200
        assert "data:image/png;base64," in previa.text
        assert previa.headers["Content-Security-Policy"].startswith("default-src 'none'")
        assert cliente.get("/admin/relatorios/2020-01-01").status_code == 404

        painel = cliente.get("/admin/atendimento/gestao").text
        assert "</svg>Enviado" in painel

    def test_data_futura_recusada(self, cliente: TestClient) -> None:
        token = entrar(cliente)
        resposta = cliente.post(
            "/admin/relatorios/reprocessar", data={"data": "2030-01-01", "csrf": token}
        )
        assert "data futura" in resposta.text


class TestDashboard:
    @staticmethod
    def metricas(atendimentos: int, faltas: int) -> dict[str, object]:
        agendados = atendimentos + faltas
        return {
            "kpis": {
                "agendados": agendados,
                "atendimentos": atendimentos,
                "faltas": faltas,
                "espera_media_s": 600,
                "tma_s": 900,
                "atendimentos_medidos": atendimentos,
            },
            "por_turno": {},
            "alertas": {"quantidade": 1},
        }

    def test_mes_ate_ontem_comparado_com_o_mes_anterior(
        self, cliente: TestClient, uow: FabricaUoW, relogio: RelogioFixo
    ) -> None:
        with uow() as u:
            for dia, atendimentos in ((DIA, 8), (DIA.replace(month=8, day=10), 4)):
                u.resumos.salvar_metricas(
                    dia, self.metricas(atendimentos, 2), "1.3.0", relogio.instante
                )
            u.resumos.salvar_metricas(  # fora do intervalo do mês anterior (1–23/08)
                DIA.replace(month=8, day=30), self.metricas(50, 0), "1.3.0", relogio.instante
            )
            u.commit()
        entrar(cliente)
        pagina = cliente.get("/admin/atendimento").text
        assert "01/09/2026 a 23/09/2026" in pagina  # hoje é 24/09: até ontem
        assert "dados consolidados até 23/09/2026" in pagina
        assert "comparado com 01/08 a 23/08" in pagina
        assert "▲ +100,0%" in pagina  # 8 atendimentos contra 4
        assert (
            'id="dados-grafico-atendimento"' in pagina
            and "/static/admin/chart.umd.min.js" in pagina
        )
        assert "Sem dados consolidados" not in pagina
        assert "kpi-detalhe" not in pagina  # resumos 1.3 não têm tópicos

    def test_topicos_com_detalhe_por_tipo_de_exame(
        self, cliente: TestClient, uow: FabricaUoW, relogio: RelogioFixo
    ) -> None:
        agendas = {
            10: agenda(10, "Clínico", sala="Consultório 1"),
            20: agenda(20, "Audiometria", tipo=TipoAgenda.SALA_EXAMES),
        }
        m = calcular_metricas(
            [
                atendimento(1, chamada="08:00", fim="08:20", tipo_exame="Admissional"),
                atendimento(2, id_agenda=20, chamada="08:00", fim="08:10", tipo_exame="Periódico"),
            ],
            agendas,
            RegrasMetricas(),
            hora("00:00"),
            hora("23:59:59"),
        )
        resumo = ResumoDiario(DIA, ("Todas",), hora("23:00"), m, comparar(m.kpis, None, DIA))
        with uow() as u:
            u.resumos.salvar_metricas(DIA, resumo.para_json(), "1.4.0", relogio.instante)
            u.commit()
        entrar(cliente)
        pagina = cliente.get("/admin/atendimento").text
        assert "<h2>Salas de exames</h2>" in pagina and "<h2>Consultórios</h2>" in pagina
        assert "Guichês (recepção)" not in pagina  # tópico sem movimento não aparece
        assert pagina.count('<details class="card kpi kpi-detalhe">') == 6
        assert "Tempo de consulta" in pagina and "Mudança de Riscos Ocupacionais" in pagina

    def test_periodo_vazio_e_periodo_escolhido(self, cliente: TestClient) -> None:
        entrar(cliente)
        assert "Sem dados consolidados neste período" in cliente.get("/admin/atendimento").text
        pagina = cliente.get("/admin/atendimento?inicio=2026-09-10&fim=2026-09-20").text
        assert "10/09/2026 a 20/09/2026" in pagina

    @pytest.mark.parametrize(
        ("consulta", "erro"),
        [
            ("inicio=2026-08-01&fim=2026-09-20", "no máximo 31 dias"),
            ("inicio=2026-09-20&fim=2026-09-10", "igual ou posterior à data inicial"),
            ("inicio=2026-09-20&fim=2026-09-30", "não pode ser no futuro"),
            ("inicio=x&fim=", "Informe a data inicial e a data final"),
        ],
    )
    def test_periodo_invalido_volta_ao_padrao(
        self, cliente: TestClient, consulta: str, erro: str
    ) -> None:
        entrar(cliente)
        pagina = cliente.get(f"/admin/atendimento?{consulta}").text
        assert erro in pagina
        assert "01/09/2026 a 23/09/2026" in pagina


class TestDashboardsFinanceiroESesmt:
    """Hoje é 24/09 (relógio dos testes): o padrão é 01/09 a 23/09, contra 01/08 a 23/08."""

    def salvar(self, uow: FabricaUoW, repositorio: str, dias: dict[str, dict[str, object]]) -> None:
        from datetime import date  # noqa: PLC0415

        gerado = hora("05:45")
        with uow() as u:
            for dia, metricas in dias.items():
                getattr(u, repositorio).salvar_metricas(
                    date.fromisoformat(dia), metricas, "1.0.0", gerado
                )
            u.commit()

    def test_financeiro(self, cliente: TestClient, uow: FabricaUoW) -> None:
        def dia(recebido: float, mrr: float) -> dict[str, object]:
            return {
                "caixa": {"recebido_dia": recebido, "pago_dia": 100.0},
                "faturamento": {"dia": 50.0, "titulos_dia": 1, "categorias": ["fora do recorte"]},
                "recorrente": {"mrr": mrr, "clientes_mrr": 3, "contratos_ativos": 7},
                "contratos_a_vencer": [{"mensalidade": 80.0}],
            }

        entrar(cliente)
        assert "Sem resumos neste período" in cliente.get("/admin/financeiro").text
        self.salvar(
            uow,
            "resumos_financeiros",
            {"2026-09-23": dia(300.0, 1100.0), "2026-08-23": dia(150.0, 1000.0)},
        )
        pagina = cliente.get("/admin/financeiro").text
        assert "01/09/2026 a 23/09/2026" in pagina
        assert "comparado com 01/08 a 23/08" in pagina and "Em 23/09" in pagina
        assert "R$ 300,00" in pagina and "▲ +100,0%" in pagina
        assert "R$ 80,00 em mensalidades" in pagina
        assert 'id="dados-grafico-caixa"' in pagina and "Saldo acumulado" in pagina
        assert 'href="/admin/financeiro/gestao" class="btn btn-secundario"' in pagina
        assert "Desligado" in pagina  # FINANCEIRO_HABILITADO=false nos testes
        assert re.search(r'href="/admin/financeiro"[^>]*aria-current="page"', pagina)
        erro = cliente.get("/admin/financeiro?inicio=2026-08-01&fim=2026-09-20").text
        assert "no máximo 31 dias" in erro

    def test_sesmt(self, cliente: TestClient, uow: FabricaUoW) -> None:
        from datetime import date, datetime  # noqa: PLC0415

        from relatorio.domain.entidades import FUSO_BRASILIA  # noqa: PLC0415
        from relatorio.domain.sesmt import (  # noqa: PLC0415
            DadosSesmt,
            DocumentoSst,
            EmpresaSesmt,
            EventoEsocial,
            calcular_sesmt,
            mes_seguinte,
        )

        entrar(cliente)
        assert "Nenhuma coleta ainda" in cliente.get("/admin/sesmt").text
        hoje = datetime.now(FUSO_BRASILIA).date()  # a tela usa o relógio real
        vence, _ = mes_seguinte(hoje)
        empresas = {
            i: EmpresaSesmt(i, f"Empresa {i:02d} LTDA", "", "1", True) for i in range(1, 31)
        }
        documentos = [DocumentoSst(i, "PGR", date(2020, 1, 1)) for i in (1, 2)]
        documentos += [DocumentoSst(i, "PCMSO", vence) for i in empresas]
        eventos = [EventoEsocial("1", "S-2220", 3, hoje.replace(day=1), "")]
        with uow() as u:
            for ref in (hoje - timedelta(days=2), hoje - timedelta(days=1)):
                dados = DadosSesmt(ref, hoje, empresas, documentos, eventos, {"1": "PREMIUM"})
                gerado = datetime.combine(ref, datetime.min.time(), FUSO_BRASILIA)
                u.resumos_sesmt.salvar_metricas(ref, calcular_sesmt(dados, gerado), "2.0.0", gerado)
            # Resumo da versão antiga: fica de fora do dashboard e do histórico.
            u.resumos_sesmt.salvar_metricas(
                hoje - timedelta(days=3),
                {"versao_regra": "1.1.0", "vencidos": {"total": 9}},
                "1.1.0",
                gerado,
            )
            u.commit()
        pagina = cliente.get("/admin/sesmt").text
        assert '<p class="manchete">2 documentos vencidos em 2 empresas; 30 vencem em' in pagina
        assert 'id="dados-grafico-grupos"' in pagina and '"horizontal": true' in pagina
        assert 'id="dados-grafico-vencidos-dia"' in pagina
        assert 'id="dados-grafico-esocial-mensal"' in pagina
        assert "■ estável" in pagina  # vencidos: 2 contra 2 na coleta anterior
        # Lista paginada: 25 por página por padrão, com seletor e páginas.
        assert pagina.count("Empresa ") >= 25 and "1–25 de 30" in pagina
        assert 'name="vencem_n"' in pagina
        segunda = cliente.get("/admin/sesmt?vencem_p=2").text
        assert "26–30 de 30" in segunda
        assert "eSocial rejeitados em" in pagina and "S\u20112220: 1" in pagina
        assert re.search(r'href="/admin/sesmt"[^>]*aria-current="page"', pagina)
        assert "Planilha (LGPD)" not in pagina and "Exportar planilha" not in pagina


class TestMonitor:
    HOJE = DIA + timedelta(days=1)

    def semear(self, uow: FabricaUoW) -> None:
        with uow() as u:
            for e in (
                evento(1, None, Situacao.AGUARDANDO, "08:00", self.HOJE),
                evento(1, Situacao.AGUARDANDO, Situacao.EM_ATENDIMENTO, "08:10", self.HOJE),
                evento(1, Situacao.EM_ATENDIMENTO, Situacao.ATENDIDO, "08:25", self.HOJE),
                evento(2, None, Situacao.AGUARDANDO, "09:40", self.HOJE),
            ):
                u.eventos.inserir(e)
            u.snapshots.salvar(snapshot(1, Situacao.ATENDIDO, data_agendamento=self.HOJE))
            u.snapshots.salvar(snapshot(2, Situacao.AGUARDANDO, data_agendamento=self.HOJE))
            u.commit()

    def test_exige_login(self, cliente: TestClient) -> None:
        pagina = cliente.get("/admin/monitor", follow_redirects=False)
        assert pagina.status_code == 303
        assert pagina.headers["location"] == "/login"
        fragmento = cliente.get("/admin/monitor/dados", headers={"HX-Request": "true"})
        assert fragmento.status_code == 401
        assert fragmento.headers["HX-Redirect"] == "/login"

    def test_pagina_e_fragmento(self, cliente: TestClient, uow: FabricaUoW) -> None:
        self.semear(uow)
        entrar(cliente)
        pagina = cliente.get("/admin/monitor")
        assert pagina.status_code == 200
        assert re.search(
            r'aria-current="page">[^<]*(<[^>]+>[^<]*)*Atendimento ao VIVO</span></a>', pagina.text
        )
        assert "/static/admin/monitor.js" in pagina.text
        assert "1 pessoa na recepção agora; a maior espera é de 20 min." in pagina.text
        assert "Movimento por hora" in pagina.text

        fragmento = cliente.get("/admin/monitor/dados", headers={"HX-Request": "true"})
        assert fragmento.status_code == 200
        assert fragmento.headers["Cache-Control"] == "no-store"
        assert "<html" not in fragmento.text
        assert "Na recepção agora" in fragmento.text
        assert "data-dica=" in fragmento.text
        assert cliente.get("/static/admin/monitor.js").status_code == 200
        estatico = cliente.get("/static/admin/admin.css")
        assert estatico.headers["cache-control"] == "no-cache"  # deploy novo vale na hora
        assert estatico.headers["etag"]

    def test_dia_sem_agendamentos(self, cliente: TestClient) -> None:
        entrar(cliente)
        texto = cliente.get("/admin/monitor").text
        assert "Ainda não há agendamentos hoje" in texto
        assert "Ninguém aguardando na recepção agora." in texto


class TestFinanceiro:
    def test_pagina_destinatarios_centros_e_previa(
        self, cliente: TestClient, uow: FabricaUoW
    ) -> None:
        from datetime import date, datetime  # noqa: PLC0415

        from relatorio.domain.entidades import FUSO_BRASILIA  # noqa: PLC0415
        from relatorio.domain.financeiro import (  # noqa: PLC0415
            DadosFinanceiros,
            calcular_financeiro,
        )

        token = entrar(cliente)
        pagina = cliente.get("/admin/financeiro/gestao")
        assert pagina.status_code == 200
        assert "Gestão do relatório financeiro" in pagina.text and "&larr; Dashboard" in pagina.text
        assert "FINANCEIRO_HABILITADO=false" in pagina.text
        assert re.search(r'href="/admin/financeiro"[^>]*aria-current="page"', pagina.text)

        ok = cliente.post(
            "/admin/financeiro/destinatarios",
            data={"email": "Diretoria@MultiLife.com.br", "csrf": token},
        )
        assert "diretoria@multilife.com.br cadastrado na lista do relatório financeiro." in ok.text
        with uow() as u:
            [d] = u.destinatarios_financeiro.listar()
            assert "diretoria@multilife.com.br" not in {x.email for x in u.destinatarios.listar()}
        linha = cliente.post(
            f"/admin/financeiro/destinatarios/{d.id}/ativo",
            data={"ativo": "false"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
        assert 'aria-checked="false"' in linha.text
        assert f'hx-post="/admin/financeiro/destinatarios/{d.id}/ativo"' in linha.text

        salvo = cliente.post(
            "/admin/financeiro/centros", data={"nomes": "3=Ocupacional; 2=Clínica", "csrf": token}
        )
        assert 'value="2=Clínica; 3=Ocupacional"' in salvo.text

        futuro = cliente.post(
            "/admin/financeiro/reprocessar", data={"data": "2099-01-01", "csrf": token}
        )
        assert "escolha até ontem" in futuro.text

        assert cliente.get("/admin/financeiro/relatorios/2026-09-27").status_code == 404
        ref = date(2026, 9, 27)
        vazio = DadosFinanceiros(ref, date(2026, 9, 28), [], [], [], [], [], [])
        metricas = calcular_financeiro(vazio, datetime(2026, 9, 28, 5, 45, tzinfo=FUSO_BRASILIA))
        with uow() as u:
            u.resumos_financeiros.salvar_metricas(
                ref, metricas, "1.0.0", datetime(2026, 9, 28, 5, 45, tzinfo=FUSO_BRASILIA)
            )
            u.commit()
        previa = cliente.get("/admin/financeiro/relatorios/2026-09-27")
        assert previa.status_code == 200
        assert "Relatório financeiro diário" in previa.text
        assert (
            "/admin/financeiro/relatorios/2026-09-27"
            in cliente.get("/admin/financeiro/gestao").text
        )


class TestSesmt:
    def test_pagina_destinatarios_grupos_e_previa(
        self, cliente: TestClient, uow: FabricaUoW
    ) -> None:
        from datetime import date, datetime  # noqa: PLC0415

        from relatorio.domain.entidades import FUSO_BRASILIA  # noqa: PLC0415
        from relatorio.domain.sesmt import DadosSesmt, calcular_sesmt  # noqa: PLC0415

        token = entrar(cliente)
        pagina = cliente.get("/admin/sesmt/gestao")
        assert pagina.status_code == 200
        assert "Gestão do relatório do SESMT" in pagina.text and "&larr; Dashboard" in pagina.text
        assert "SESMT_HABILITADO=false" in pagina.text
        assert re.search(r'href="/admin/sesmt"[^>]*aria-current="page"', pagina.text)
        assert "1=GESTÃO PREMIUM" in pagina.text  # padrão dos grupos do SGG

        ok = cliente.post(
            "/admin/sesmt/destinatarios",
            data={"email": "SESMT@MultiLife.com.br", "csrf": token},
        )
        assert "sesmt@multilife.com.br cadastrado na lista do relatório do SESMT." in ok.text
        with uow() as u:
            [d] = u.destinatarios_sesmt.listar()
            assert not u.destinatarios.listar() and not u.destinatarios_financeiro.listar()
        linha = cliente.post(
            f"/admin/sesmt/destinatarios/{d.id}/ativo",
            data={"ativo": "false"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
        assert 'aria-checked="false"' in linha.text
        assert f'hx-post="/admin/sesmt/destinatarios/{d.id}/ativo"' in linha.text

        salvo = cliente.post(
            "/admin/sesmt/grupos", data={"nomes": "2=Gestão 2; 1=Premium", "csrf": token}
        )
        assert 'value="1=Premium; 2=Gestão 2"' in salvo.text

        futuro = cliente.post(
            "/admin/sesmt/reprocessar", data={"data": "2099-01-01", "csrf": token}
        )
        assert "Escolha uma data até hoje." in futuro.text

        assert cliente.get("/admin/sesmt/relatorios/2026-09-29").status_code == 404
        ref = date(2026, 9, 29)
        vazio = DadosSesmt(ref, date(2026, 9, 30), {}, [], [])
        gerado = datetime(2026, 9, 29, 23, 0, tzinfo=FUSO_BRASILIA)
        with uow() as u:
            u.resumos_sesmt.salvar_metricas(ref, calcular_sesmt(vazio, gerado), "2.0.0", gerado)
            antigo = {"versao_regra": "1.1.0", "a_vencer": {"total": 1}}
            u.resumos_sesmt.salvar_metricas(date(2026, 9, 28), antigo, "1.1.0", gerado)
            u.commit()
        assert cliente.get("/admin/sesmt/relatorios/2026-09-28").status_code == 404
        previa = cliente.get("/admin/sesmt/relatorios/2026-09-29")
        assert previa.status_code == 200
        assert "Relatório de gestão SESMT" in previa.text
        gestao = cliente.get("/admin/sesmt/gestao").text
        assert "/admin/sesmt/relatorios/2026-09-29" in gestao
        assert "/admin/sesmt/relatorios/2026-09-28" not in gestao
        assert "Formato antigo do relatório" in gestao
        assert "Processar período" not in gestao

    def test_envio_das_planilhas_de_vencimento(self, cliente: TestClient, uow: FabricaUoW) -> None:
        from datetime import date  # noqa: PLC0415

        from tests.fabricas import planilha_vencimentos  # noqa: PLC0415

        token = entrar(cliente)
        pagina = cliente.get("/admin/sesmt/gestao").text
        assert 'enctype="multipart/form-data"' in pagina
        assert "Nenhuma planilha enviada" in pagina
        xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        pgr = planilha_vencimentos("PGR", [(60, "A.M LTDA", "", "GESTÃO 2", "28/01/2028", "")])
        ltcat = planilha_vencimentos("LTCAT", [(61, "B LTDA", "", "EVENTUAL", "01/12/2026", "")])
        ok = cliente.post(
            "/admin/sesmt/planilhas",
            data={"csrf": token},
            files=[
                ("arquivos", ("pgr.xlsx", pgr, xlsx)),
                ("arquivos", ("qualquer-nome.xlsx", ltcat, xlsx)),  # o tipo vem do título
            ],
        )
        assert "Planilhas salvas: PGR (1 empresas), LTCAT (1 empresas)" in ok.text
        with uow() as u:
            salvas = {p.tipo: p for p in u.planilhas_sesmt.listar()}
        assert sorted(salvas) == ["LTCAT", "PGR"]
        [doc] = salvas["LTCAT"].documentos
        assert (doc.id_empresa, doc.empresa, doc.grupo, doc.vencimento) == (
            61,
            "B LTDA",
            "EVENTUAL",
            date(2026, 12, 1),
        )
        assert salvas["PGR"].arquivo == "pgr.xlsx"

        ruim = cliente.post(
            "/admin/sesmt/planilhas",
            data={"csrf": token},
            files=[("arquivos", ("vencimentos.csv", b"codigo;validade", "text/csv"))],
        )
        assert "vencimentos.csv: O arquivo não é uma planilha .xlsx." in ruim.text
        with uow() as u:
            assert len(u.planilhas_sesmt.listar()) == 2  # nada mudou
