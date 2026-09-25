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
from relatorio.domain.entidades import Situacao
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.app import create_app
from relatorio.interfaces.web.seguranca import gerar_hash_senha
from tests.fabricas import DIA, agenda, evento, hora, snapshot
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
            ("/admin", "Reprocessar ou reenviar uma data"),
            ("/admin/destinatarios", "Cadastrar"),
            ("/admin/agendas", "Agendas (consultórios e guichês)"),
            ("/admin/configuracoes", "Início da tarde (corte)"),
            ("/admin/execucoes", "Execuções dos jobs"),
        ],
    )
    def test_paginas_renderizam(self, cliente: TestClient, url: str, texto: str) -> None:
        entrar(cliente)
        resposta = cliente.get(url)
        assert resposta.status_code == 200
        assert texto in resposta.text
        assert "/static/brand/logo-multilife-admin.png" in resposta.text


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
        guiche = cliente.post(
            "/admin/agendas/10/guiche", data={"guiche": "true"}, headers={"X-CSRF-Token": token}
        )
        assert 'aria-checked="true"' in guiche.text and "Guichê" in guiche.text
        with uow() as u:
            assert next(a for a in u.agendas.listar() if a.id_agenda == 10).guiche is True
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

        painel = cliente.get("/admin").text
        assert "</svg>Enviado" in painel

    def test_data_futura_recusada(self, cliente: TestClient) -> None:
        token = entrar(cliente)
        resposta = cliente.post(
            "/admin/relatorios/reprocessar", data={"data": "2030-01-01", "csrf": token}
        )
        assert "data futura" in resposta.text


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
        assert 'aria-current="page">Monitor</a>' in pagina.text
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

    def test_dia_sem_agendamentos(self, cliente: TestClient) -> None:
        entrar(cliente)
        texto = cliente.get("/admin/monitor").text
        assert "Ainda não há agendamentos hoje" in texto
        assert "Ninguém aguardando na recepção agora." in texto
