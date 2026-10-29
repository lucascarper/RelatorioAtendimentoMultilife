"""Esqueci minha senha e "Enviar acesso" sobre o banco em memória (sem PostgreSQL)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relatorio.application.ports import EmailDuplicado, FabricaUoW
from relatorio.config import Settings
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.app import create_app
from relatorio.interfaces.web.seguranca import conferir_senha, gerar_hash_senha
from tests.fabricas import hora
from tests.fakes import BancoEmMemoria, EmailFake, RelogioFixo, UoWEmMemoria

RAIZ = Path(__file__).resolve().parents[2]
SENHA_ADMIN = "senha-do-admin-123"
URL_USUARIOS = "/admin/configuracoes/usuarios"


class ContainerEmMemoria(Container):
    def __init__(self, settings: Settings, banco: BancoEmMemoria) -> None:
        super().__init__(settings)
        self.banco = banco

    @property
    def uow(self) -> FabricaUoW:
        return lambda: UoWEmMemoria(self.banco)


@pytest.fixture
def banco() -> BancoEmMemoria:
    banco = BancoEmMemoria()
    UoWEmMemoria(banco).usuarios.criar(
        "Ana Silva", "ana", gerar_hash_senha("senha-antiga-1"), ["financeiro"], "Ana@Multilife.com"
    )
    return banco


@pytest.fixture
def email() -> EmailFake:
    return EmailFake()


@pytest.fixture
def relogio() -> RelogioFixo:
    return RelogioFixo(hora("10:00"))


@pytest.fixture
def cliente(
    banco: BancoEmMemoria, email: EmailFake, relogio: RelogioFixo, tmp_path: Path
) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        admin_user="admin",
        admin_password_hash=gerar_hash_senha(SENHA_ADMIN),  # type: ignore[arg-type]
        secret_key="x" * 40,  # type: ignore[arg-type]
        admin_url="https://relatorios.exemplo.com/admin",
        templates_dir=RAIZ / "templates",
        static_dir=RAIZ / "static",
        email_saida_dir=tmp_path,
    )
    container = ContainerEmMemoria(settings, banco)
    container.__dict__["email"] = email
    container.__dict__["relogio"] = relogio
    with TestClient(create_app(settings, container)) as c:
        yield c


def csrf(cliente: TestClient, url: str) -> str:
    achado = re.search(r'name="csrf" value="([^"]+)"', cliente.get(url).text)
    assert achado, "página sem token CSRF"
    return achado.group(1)


def pedir(cliente: TestClient, email: str) -> str:
    resposta = cliente.post(
        "/esqueci-senha", data={"email": email, "csrf": csrf(cliente, "/esqueci-senha")}
    )
    return f"{resposta.status_code} {resposta.text}"


def link_do_email(email: EmailFake) -> str:
    achado = re.search(
        r"https://relatorios\.exemplo\.com(/redefinir-senha/[\w-]+)", email.enviados[-1][1].texto
    )
    assert achado, "e-mail sem link"
    return achado.group(1)


def redefinir(cliente: TestClient, caminho: str, senha: str, confirmacao: str | None = None) -> int:
    resposta = cliente.post(
        caminho,
        data={"senha": senha, "confirmacao": confirmacao or senha, "csrf": csrf(cliente, "/login")},
        follow_redirects=False,
    )
    return resposta.status_code


def senha_da_ana(banco: BancoEmMemoria) -> str:
    ana = UoWEmMemoria(banco).usuarios.obter_por_usuario("ana")
    assert ana is not None
    return ana.senha_hash


class TestEsqueciSenha:
    def test_login_tem_o_link(self, cliente: TestClient) -> None:
        assert 'href="/esqueci-senha"' in cliente.get("/login").text
        assert "Enviar link" in cliente.get("/esqueci-senha").text

    def test_email_sem_perfil_avisa(self, cliente: TestClient, email: EmailFake) -> None:
        resposta = pedir(cliente, "ninguem@multilife.com")
        assert (
            resposta.startswith("404")
            and "Não existe perfil cadastrado com esse e-mail" in resposta
        )
        assert email.enviados == []

    def test_email_invalido(self, cliente: TestClient) -> None:
        assert pedir(cliente, "nao-e-email").startswith("422")

    def test_fluxo_completo(
        self, cliente: TestClient, email: EmailFake, banco: BancoEmMemoria
    ) -> None:
        resposta = pedir(cliente, "  ANA@multilife.com ")
        assert resposta.startswith("200") and "Enviamos um link para" in resposta
        [(para, conteudo)] = email.enviados
        assert para == ["ana@multilife.com"]
        assert conteudo.assunto.startswith("Redefinir senha")
        assert "Seu usuário: ana" in conteudo.texto and "1 hora" in conteudo.texto
        caminho = link_do_email(email)
        token = caminho.rsplit("/", 1)[1]
        # O banco guarda só o SHA-256 do token.
        assert list(banco.tokens_senha) == [hashlib.sha256(token.encode()).hexdigest()]

        pagina = cliente.get(caminho)
        assert pagina.status_code == 200 and "<strong>ana</strong>" in pagina.text
        assert redefinir(cliente, caminho, "curta") == 422
        assert redefinir(cliente, caminho, "senha-nova-123", "outra-senha-123") == 422
        assert redefinir(cliente, caminho, "senha-nova-123") == 303
        assert conferir_senha("senha-nova-123", senha_da_ana(banco))
        assert "Senha definida" in cliente.get("/login").text

        # Uso único: o mesmo link não serve de novo.
        assert cliente.get(caminho).status_code == 404
        assert redefinir(cliente, caminho, "outra-senha-456") == 404
        assert conferir_senha("senha-nova-123", senha_da_ana(banco))

    def test_link_vence_em_1_hora(
        self, cliente: TestClient, email: EmailFake, relogio: RelogioFixo
    ) -> None:
        pedir(cliente, "ana@multilife.com")
        caminho = link_do_email(email)
        relogio.avancar(minutes=59)
        assert cliente.get(caminho).status_code == 200
        relogio.avancar(minutes=1)
        assert "inválido, já foi usado ou venceu" in cliente.get(caminho).text
        assert redefinir(cliente, caminho, "senha-nova-123") == 404

    def test_novo_link_usado_invalida_os_anteriores(
        self, cliente: TestClient, email: EmailFake
    ) -> None:
        pedir(cliente, "ana@multilife.com")
        primeiro = link_do_email(email)
        pedir(cliente, "ana@multilife.com")
        assert redefinir(cliente, link_do_email(email), "senha-nova-123") == 303
        assert cliente.get(primeiro).status_code == 404

    def test_token_inventado(self, cliente: TestClient) -> None:
        assert cliente.get("/redefinir-senha/qualquer-coisa").status_code == 404

    def test_limite_de_pedidos(self, cliente: TestClient, email: EmailFake) -> None:
        for _ in range(5):
            assert pedir(cliente, "ninguem@multilife.com").startswith("404")
        assert pedir(cliente, "ana@multilife.com").startswith("429")
        assert email.enviados == []

    def test_envios_nao_gastam_o_limite_do_ip(
        self, cliente: TestClient, email: EmailFake, banco: BancoEmMemoria
    ) -> None:
        # Várias pessoas atrás do mesmo IP: cada envio conta só para o e-mail dela.
        UoWEmMemoria(banco).usuarios.criar("Bia", "bia", "h", [], "bia@multilife.com")
        for endereco in ("ana", "bia") * 2:
            assert pedir(cliente, f"{endereco}@multilife.com").startswith("200")
        assert len(email.enviados) == 4

    def test_limite_por_email_protege_a_caixa_de_quem_recebe(
        self, cliente: TestClient, email: EmailFake
    ) -> None:
        for _ in range(5):
            assert pedir(cliente, "ana@multilife.com").startswith("200")
        resposta = pedir(cliente, "ana@multilife.com")
        assert resposta.startswith("429") and "Já enviamos vários links" in resposta
        assert len(email.enviados) == 5

    def test_falha_no_envio(self, cliente: TestClient, email: EmailFake) -> None:
        email.falhar = True
        assert pedir(cliente, "ana@multilife.com").startswith("503")


def entrar_admin(cliente: TestClient) -> str:
    cliente.post(
        "/login",
        data={"usuario": "admin", "senha": SENHA_ADMIN, "csrf": csrf(cliente, "/login")},
    )
    return csrf(cliente, URL_USUARIOS)


class TestEnviarAcesso:
    def test_envia_login_e_link_de_24_horas(
        self, cliente: TestClient, email: EmailFake, banco: BancoEmMemoria
    ) -> None:
        token = entrar_admin(cliente)
        lista = cliente.get(URL_USUARIOS).text
        assert "ana@multilife.com" in lista and f"{URL_USUARIOS}/1/enviar-acesso" in lista
        resposta = cliente.post(
            f"{URL_USUARIOS}/1/enviar-acesso", data={"csrf": token}, headers={"HX-Request": "true"}
        )
        assert resposta.headers["HX-Redirect"] == URL_USUARIOS
        [(para, conteudo)] = email.enviados
        assert para == ["ana@multilife.com"] and conteudo.assunto.startswith("Seu acesso")
        assert "Seu usuário: ana" in conteudo.texto and "24 horas" in conteudo.texto
        assert "enviado para ana@multilife.com" in cliente.get(URL_USUARIOS).text
        assert redefinir(cliente, link_do_email(email), "senha-nova-123") == 303
        assert conferir_senha("senha-nova-123", senha_da_ana(banco))

    def test_usuario_sem_email(self, cliente: TestClient, email: EmailFake) -> None:
        token = entrar_admin(cliente)
        cliente.post(
            URL_USUARIOS,
            data={"nome": "Bia", "usuario": "bia", "senha": "senha-da-bia-1", "csrf": token},
        )
        assert "Sem e-mail" in cliente.get(URL_USUARIOS).text
        resposta = cliente.post(f"{URL_USUARIOS}/2/enviar-acesso", data={"csrf": token})
        assert "Cadastre o e-mail de bia" in resposta.text
        assert email.enviados == []

    def test_exige_login_e_csrf(self, cliente: TestClient, email: EmailFake) -> None:
        url = f"{URL_USUARIOS}/1/enviar-acesso"
        assert cliente.post(url, follow_redirects=False).headers["location"] == "/login"
        entrar_admin(cliente)
        assert cliente.post(url).status_code == 403
        assert email.enviados == []


class TestCadastroComEmail:
    def test_email_opcional_valido_e_unico(
        self, cliente: TestClient, banco: BancoEmMemoria
    ) -> None:
        token = entrar_admin(cliente)
        dados = {"nome": "Bia", "usuario": "bia", "senha": "senha-da-bia-1", "csrf": token}
        repetido = cliente.post(URL_USUARIOS, data={**dados, "email": "ANA@multilife.com"})
        assert (
            repetido.status_code == 422 and "Já existe um usuário com esse e-mail" in repetido.text
        )
        invalido = cliente.post(URL_USUARIOS, data={**dados, "email": "bia@"})
        assert invalido.status_code == 422 and "e-mail válido" in invalido.text
        cliente.post(URL_USUARIOS, data={**dados, "email": " Bia@Multilife.com "})
        bia = UoWEmMemoria(banco).usuarios.obter_por_usuario("bia")
        assert bia is not None and bia.email == "bia@multilife.com"
        # Editar sem e-mail tira o e-mail.
        cliente.post(
            f"{URL_USUARIOS}/{bia.id}", data={"nome": "Bia", "usuario": "bia", "csrf": token}
        )
        assert UoWEmMemoria(banco).usuarios.obter(bia.id).email is None  # type: ignore[union-attr]


def test_repositorio_em_memoria_email_e_tokens() -> None:
    banco = BancoEmMemoria()
    u = UoWEmMemoria(banco)
    ana = u.usuarios.criar("Ana", "ana", "h", [], "ana@x.com")
    bia = u.usuarios.criar("Bia", "bia", "h", [])
    with pytest.raises(EmailDuplicado):
        u.usuarios.atualizar(bia.id, "Bia", "bia", [], email="ANA@x.com")
    agora = hora("10:00")
    u.tokens_senha.criar("t1", ana.id, hora("11:00"), agora)
    assert u.tokens_senha.valido("t1", agora) == ana.id
    assert u.tokens_senha.valido("t1", hora("11:00")) is None  # venceu
    u.tokens_senha.criar("t2", ana.id, hora("13:00"), hora("12:00"))
    assert list(banco.tokens_senha) == ["t2"]  # criar apaga os vencidos
    u.usuarios.excluir(ana.id)
    assert u.tokens_senha.consumir("t2", agora) is None  # usuário excluído leva o link
