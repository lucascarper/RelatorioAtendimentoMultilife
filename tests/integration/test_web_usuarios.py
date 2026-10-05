"""Módulos do admin e painel de usuários (Configurações → Usuários) contra o PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from relatorio.application.ports import FabricaUoW, UsuarioDuplicado
from relatorio.infrastructure.container import Container
from relatorio.interfaces.web.app import create_app
from relatorio.interfaces.web.seguranca import gerar_hash_senha
from tests.integration.test_web import (  # noqa: F401
    SENHA,
    cliente,
    container,
    csrf,
    entrar,
    relogio,
)

pytestmark = pytest.mark.integration

URL = "/admin/configuracoes/usuarios"


@pytest.fixture
def outro(container: Container) -> Iterator[TestClient]:  # noqa: F811
    """Segundo navegador (sessão própria), para o usuário comum."""
    with TestClient(create_app(container.settings, container)) as c:
        yield c


def criar(
    navegador: TestClient,
    token: str,
    *,
    nome: str = "Ana Silva",
    usuario: str = "ana",
    senha: str = "senha-da-ana-1",
    modulos: tuple[str, ...] = ("financeiro",),
) -> httpx.Response:
    return navegador.post(
        URL,
        data={
            "nome": nome,
            "usuario": usuario,
            "senha": senha,
            "modulos": list(modulos),
            "csrf": token,
        },
    )


def entrar_como(c: TestClient, usuario: str, senha: str) -> int:
    resposta = c.post(
        "/login",
        data={"usuario": usuario, "senha": senha, "csrf": csrf(c)},
        follow_redirects=False,
    )
    return resposta.status_code


def itens_do_menu(html: str) -> list[str]:
    import re  # noqa: PLC0415

    menu = re.search(r'<nav class="menu".*?</nav>', html, re.S)
    assert menu, "página sem menu"
    return re.findall(r">([^<]+)</a>", menu.group(0))


class TestModulosDoMenu:
    def test_administrador_ve_os_cinco_modulos_na_ordem(self, cliente: TestClient) -> None:  # noqa: F811
        entrar(cliente)
        pagina = cliente.get("/admin/atendimento")
        assert itens_do_menu(pagina.text) == [
            "Atendimento",
            "Financeiro",
            "SESMT",
            "Configurações",
            "Execuções",
        ]
        assert "Sistema de Relatórios" in pagina.text
        assert 'href="/admin/atendimento" aria-current="page"' in pagina.text

    def test_atendimento_reune_painel_destinatarios_e_atalhos(self, cliente: TestClient) -> None:  # noqa: F811
        entrar(cliente)
        pagina = cliente.get("/admin/atendimento").text
        assert "Reprocessar ou reenviar uma data" in pagina
        assert 'id="destinatarios"' in pagina and "Cadastrar" in pagina
        assert 'href="/admin/monitor" class="btn btn-primario"' in pagina
        assert "Acompanhar AO VIVO" in pagina
        assert 'href="/admin/agendas" class="btn btn-secundario"' in pagina
        assert "Configurar Agendas" in pagina
        # As telas de apoio voltam para o módulo, e o menu continua em Atendimento.
        monitor = cliente.get("/admin/monitor").text
        assert 'href="/admin/atendimento"' in monitor
        assert 'href="/admin/atendimento" aria-current="page"' in monitor
        agendas = cliente.get("/admin/agendas").text
        assert "&larr; Atendimento" in agendas
        assert 'href="/admin/atendimento" aria-current="page"' in agendas

    def test_inicio_leva_ao_primeiro_modulo_e_links_antigos_continuam(
        self,
        cliente: TestClient,  # noqa: F811
    ) -> None:
        entrar(cliente)
        inicio = cliente.get("/admin", follow_redirects=False)
        assert (inicio.status_code, inicio.headers["location"]) == (303, "/admin/atendimento")
        antigo = cliente.get("/admin/destinatarios", follow_redirects=False)
        assert antigo.headers["location"] == "/admin/atendimento#destinatarios"

    def test_configuracoes_tem_as_abas_regras_e_usuarios(self, cliente: TestClient) -> None:  # noqa: F811
        entrar(cliente)
        pagina = cliente.get("/admin/configuracoes").text
        assert 'class="abas"' in pagina and "Regras do relatório" in pagina
        assert f'href="{URL}"' in pagina
        assert 'href="/admin/configuracoes" aria-current="page"' in pagina


class TestCadastro:
    def test_lista_mostra_o_administrador_do_deploy(self, cliente: TestClient) -> None:  # noqa: F811
        entrar(cliente)
        pagina = cliente.get(URL)
        assert pagina.status_code == 200
        assert "Administrador do sistema" in pagina.text and "Todos os módulos" in pagina.text
        assert "Adicionar usuário" in pagina.text
        assert 'aria-current="page">Usuários</a>' in pagina.text
        form = cliente.get(f"{URL}/novo").text
        for rotulo in ("Atendimento", "Financeiro", "SESMT", "Configurações", "Execuções"):
            assert f"<strong>{rotulo}</strong>" in form
        assert "Permissões de visualização" in form

    def test_cria_com_permissoes_e_guarda_a_senha_com_hash(
        self,
        cliente: TestClient,  # noqa: F811
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        resposta = criar(
            cliente, token, usuario="  Ana.Silva ", modulos=("sesmt", "financeiro", "x")
        )
        assert "Usuário ana.silva criado." in resposta.text
        with uow() as u:
            [ana] = u.usuarios.listar()
        assert (ana.nome, ana.usuario, ana.permissoes) == (
            "Ana Silva",
            "ana.silva",
            ("financeiro", "sesmt"),  # na ordem dos módulos, sem a chave inventada
        )
        assert ana.senha_hash.startswith("$2") and "senha-da-ana-1" not in ana.senha_hash
        lista = cliente.get(URL).text
        assert "Ana Silva" in lista and "ana.silva" in lista and "Financeiro" in lista

    @pytest.mark.parametrize(
        ("campos", "erro"),
        [
            ({"nome": ""}, "Informe o nome"),
            ({"usuario": "a b"}, "O usuário deve ter"),
            ({"senha": "curta"}, "ao menos 8 caracteres"),
            ({"senha": ""}, "ao menos 8 caracteres"),
            ({"usuario": "ADMIN"}, "reservado ao administrador"),
        ],
    )
    def test_validacoes_voltam_ao_formulario_sem_perder_o_digitado(
        self,
        cliente: TestClient,  # noqa: F811
        uow: FabricaUoW,
        campos: dict[str, str],
        erro: str,
    ) -> None:
        token = entrar(cliente)
        resposta = criar(cliente, token, **campos)
        assert resposta.status_code == 422
        assert erro in resposta.text
        assert "Permissões de visualização" in resposta.text
        with uow() as u:
            assert u.usuarios.listar() == []
        if "usuario" not in campos:
            assert 'value="ana"' in resposta.text

    def test_login_duplicado_mesmo_com_outra_caixa(self, cliente: TestClient) -> None:  # noqa: F811
        token = entrar(cliente)
        criar(cliente, token)
        repetido = criar(cliente, token, nome="Outra Ana", usuario="ANA")
        assert repetido.status_code == 422
        assert "Já existe um usuário com esse login." in repetido.text

    def test_sem_csrf_recusa(self, cliente: TestClient) -> None:  # noqa: F811
        entrar(cliente)
        resposta = cliente.post(URL, data={"nome": "Ana", "usuario": "ana", "senha": "12345678"})
        assert resposta.status_code == 403


class TestLoginEPermissoes:
    def preparar(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        modulos: tuple[str, ...],
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token, modulos=modulos)
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303

    def test_menu_e_acesso_so_dos_modulos_liberados(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
    ) -> None:
        self.preparar(cliente, outro, ("financeiro", "sesmt"))
        inicio = outro.get("/admin", follow_redirects=False)
        assert inicio.headers["location"] == "/admin/financeiro"  # primeiro módulo liberado
        pagina = outro.get("/admin/financeiro")
        assert pagina.status_code == 200
        assert itens_do_menu(pagina.text) == ["Financeiro", "SESMT"]
        assert "Ana Silva" in pagina.text  # o topo mostra o nome, não o login
        assert outro.get("/admin/sesmt").status_code == 200

        bloqueada = outro.get("/admin/atendimento")
        assert bloqueada.status_code == 403
        assert "Sem acesso ao módulo Atendimento" in bloqueada.text
        assert itens_do_menu(bloqueada.text) == ["Financeiro", "SESMT"]
        for url in (
            "/admin/execucoes",
            "/admin/configuracoes",
            URL,
            "/admin/monitor",
            "/admin/agendas",
        ):
            assert outro.get(url).status_code == 403, url

    def test_acoes_de_um_modulo_sem_permissao_sao_recusadas(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        self.preparar(cliente, outro, ("financeiro",))
        token = csrf(outro, "/admin/financeiro")
        recusa = outro.post(
            "/admin/destinatarios", data={"email": "x@multilife.com.br", "csrf": token}
        )
        assert recusa.status_code == 403
        htmx = outro.post(
            "/admin/agendas/1/incluir", headers={"X-CSRF-Token": token, "HX-Request": "true"}
        )
        assert htmx.status_code == 403
        novo = outro.post(
            URL, data={"nome": "X", "usuario": "xyz", "senha": "12345678", "csrf": token}
        )
        assert novo.status_code == 403  # quem não tem Configurações não cria usuário
        with uow() as u:
            assert [x.usuario for x in u.usuarios.listar()] == ["ana"]
            assert u.destinatarios.listar() == []

    def test_usuario_sem_nenhum_modulo(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
    ) -> None:
        self.preparar(cliente, outro, ())
        pagina = outro.get("/admin")
        assert pagina.status_code == 200
        assert "Nenhum módulo liberado" in pagina.text
        assert itens_do_menu(pagina.text) == []

    def test_com_configuracoes_gerencia_usuarios_e_sem_senha_errada(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
    ) -> None:
        self.preparar(cliente, outro, ("configuracoes",))
        assert outro.get(URL).status_code == 200
        errado = outro.post(
            "/login", data={"usuario": "ana", "senha": "errada-errada", "csrf": csrf(outro)}
        )
        assert errado.status_code == 401
        inexistente = outro.post(
            "/login", data={"usuario": "fantasma", "senha": SENHA, "csrf": csrf(outro)}
        )
        assert inexistente.status_code == 401 and "Usuário ou senha inválidos." in inexistente.text

    def test_o_administrador_do_deploy_continua_entrando(self, cliente: TestClient) -> None:  # noqa: F811
        token = entrar(cliente)
        criar(cliente, token, usuario="admin2")
        assert cliente.get(URL).status_code == 200  # não perdeu nada por existirem outros


class TestEdicao:
    def test_edita_nome_modulos_e_mantem_a_senha_em_branco(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token)
        with uow() as u:
            [ana] = u.usuarios.listar()
        tela = cliente.get(f"{URL}/{ana.id}/editar").text
        assert 'value="Ana Silva"' in tela and "Deixe em branco para manter" in tela
        assert 'value="financeiro" checked' in tela
        resposta = cliente.post(
            f"{URL}/{ana.id}",
            data={
                "nome": "Ana Souza",
                "usuario": "ana",
                "senha": "",
                "modulos": ["execucoes"],
                "csrf": token,
            },
        )
        assert "Usuário ana atualizado." in resposta.text
        with uow() as u:
            depois = u.usuarios.obter(ana.id)
        assert depois is not None
        assert (depois.nome, depois.permissoes, depois.senha_hash) == (
            "Ana Souza",
            ("execucoes",),
            ana.senha_hash,
        )
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303  # a senha antiga vale

    def test_mudar_permissoes_vale_na_hora_sem_derrubar_a_sessao(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token, modulos=("financeiro", "sesmt"))
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
        assert outro.get("/admin/sesmt").status_code == 200
        with uow() as u:
            [ana] = u.usuarios.listar()
        cliente.post(
            f"{URL}/{ana.id}",
            data={
                "nome": "Ana",
                "usuario": "ana",
                "senha": "",
                "modulos": ["financeiro"],
                "csrf": token,
            },
        )
        assert outro.get("/admin/sesmt").status_code == 403  # perdeu o SESMT na hora
        assert outro.get("/admin/financeiro").status_code == 200  # mas continua logada

    def test_trocar_a_senha_derruba_as_sessoes_abertas(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token)
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
        assert outro.get("/admin/financeiro").status_code == 200
        with uow() as u:
            [ana] = u.usuarios.listar()
        cliente.post(
            f"{URL}/{ana.id}",
            data={
                "nome": "Ana",
                "usuario": "ana",
                "senha": "outra-senha-9",
                "modulos": ["financeiro"],
                "csrf": token,
            },
        )
        # Quem estava logado com a senha antiga (a sessão de um invasor, por exemplo) cai.
        queda = outro.get("/admin/financeiro", follow_redirects=False)
        assert (queda.status_code, queda.headers["location"]) == (303, "/login")
        novo = TestClient(outro.app)
        assert entrar_como(novo, "ana", "senha-da-ana-1") == 401
        assert entrar_como(novo, "ana", "outra-senha-9") == 303
        assert novo.get("/admin/financeiro").status_code == 200

    def test_quem_troca_a_propria_senha_continua_logado(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token, modulos=("configuracoes", "financeiro"))
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
        with uow() as u:
            [ana] = u.usuarios.listar()
        resposta = outro.post(
            f"{URL}/{ana.id}",
            data={
                "nome": "Ana",
                "usuario": "ana",
                "senha": "outra-senha-9",
                "modulos": ["configuracoes", "financeiro"],
                "csrf": csrf(outro, URL),
            },
        )
        assert "Usuário ana atualizado." in resposta.text
        assert outro.get("/admin/financeiro").status_code == 200
        assert entrar_como(TestClient(outro.app), "ana", "outra-senha-9") == 303

    def test_nao_deixa_repetir_login_de_outro_usuario(
        self,
        cliente: TestClient,  # noqa: F811
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token)
        criar(cliente, token, nome="Bia", usuario="bia")
        with uow() as u:
            bia = u.usuarios.obter_por_usuario("bia")
        assert bia is not None
        resposta = cliente.post(
            f"{URL}/{bia.id}",
            data={"nome": "Bia", "usuario": "ana", "senha": "", "csrf": token},
        )
        assert resposta.status_code == 422 and "Já existe um usuário" in resposta.text
        assert cliente.get(f"{URL}/9999/editar").status_code == 404

    def test_ninguem_tira_o_proprio_acesso_a_configuracoes(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token, modulos=("configuracoes", "financeiro"))
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
        with uow() as u:
            [ana] = u.usuarios.listar()
        tok = csrf(outro, URL)
        recusa = outro.post(
            f"{URL}/{ana.id}",
            data={
                "nome": "Ana",
                "usuario": "ana",
                "senha": "",
                "modulos": ["financeiro"],
                "csrf": tok,
            },
        )
        assert recusa.status_code == 422 and "próprio acesso a Configurações" in recusa.text
        # Mudar o próprio login mantém a sessão.
        ok = outro.post(
            f"{URL}/{ana.id}",
            data={
                "nome": "Ana",
                "usuario": "ana.nova",
                "senha": "",
                "modulos": ["configuracoes"],
                "csrf": tok,
            },
        )
        assert "Usuário ana.nova atualizado." in ok.text


class TestExclusao:
    def test_exclui_e_a_sessao_dele_cai(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token)
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
        with uow() as u:
            [ana] = u.usuarios.listar()
        resposta = cliente.post(f"{URL}/{ana.id}/excluir", data={"csrf": token})
        assert "Usuário ana excluído." in resposta.text
        with uow() as u:
            assert u.usuarios.listar() == []
        queda = outro.get("/admin/financeiro", follow_redirects=False)
        assert (queda.status_code, queda.headers["location"]) == (303, "/login")
        assert entrar_como(TestClient(outro.app), "ana", "senha-da-ana-1") == 401
        assert cliente.post(f"{URL}/{ana.id}/excluir", data={"csrf": token}).status_code == 404

    def test_botao_de_excluir_pede_confirmacao_e_recarrega_a_lista(
        self,
        cliente: TestClient,  # noqa: F811
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token)
        with uow() as u:
            [ana] = u.usuarios.listar()
        lista = cliente.get(URL).text
        assert f'hx-post="{URL}/{ana.id}/excluir"' in lista and "hx-confirm=" in lista
        htmx = cliente.post(
            f"{URL}/{ana.id}/excluir", headers={"X-CSRF-Token": token, "HX-Request": "true"}
        )
        assert htmx.status_code == 200 and htmx.headers["HX-Redirect"] == URL

    def test_ninguem_exclui_o_proprio_usuario(
        self,
        cliente: TestClient,  # noqa: F811
        outro: TestClient,
        uow: FabricaUoW,
    ) -> None:
        token = entrar(cliente)
        criar(cliente, token, modulos=("configuracoes",))
        assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
        with uow() as u:
            [ana] = u.usuarios.listar()
        resposta = outro.post(f"{URL}/{ana.id}/excluir", data={"csrf": csrf(outro, URL)})
        assert "Você não pode excluir o seu próprio usuário." in resposta.text
        with uow() as u:
            assert len(u.usuarios.listar()) == 1


class TestRepositorioSql:
    def test_unicidade_e_atualizacao(self, uow: FabricaUoW) -> None:
        with uow() as u:
            ana = u.usuarios.criar("Ana", "Ana", gerar_hash_senha("12345678"), ["sesmt"])
            u.commit()
        with uow() as u:
            with pytest.raises(UsuarioDuplicado):
                u.usuarios.criar("Outra", "ANA", "x", [])
            bia = u.usuarios.criar("Bia", "bia", "h", [])  # a sessão segue utilizável
            with pytest.raises(UsuarioDuplicado):
                u.usuarios.atualizar(bia.id, "Bia", "ana", [])
            u.usuarios.atualizar(bia.id, "Beatriz", "bia.s", ["execucoes"], senha_hash="novo")
            u.commit()
        with uow() as u:
            lido = u.usuarios.obter_por_usuario("BIA.S")
            assert lido is not None
            assert (lido.nome, lido.permissoes, lido.senha_hash) == (
                "Beatriz",
                ("execucoes",),
                "novo",
            )
            assert [x.nome for x in u.usuarios.listar()] == ["Ana", "Beatriz"]
            assert u.usuarios.obter(ana.id) is not None
            assert u.usuarios.excluir(ana.id) and not u.usuarios.excluir(ana.id)
            u.commit()
