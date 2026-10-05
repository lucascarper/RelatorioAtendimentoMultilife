"""Módulos, permissões e validações do cadastro de usuários."""

from __future__ import annotations

import pytest

from relatorio.application.ports import UsuarioDuplicado
from relatorio.domain.acesso import (
    CONFIGURACOES,
    MODULOS,
    TODOS_OS_MODULOS,
    Acesso,
    modulos_validos,
    normalizar_usuario,
    validar_nome,
    validar_senha,
    validar_usuario,
)
from tests.fakes import BancoEmMemoria, UoWEmMemoria


def test_os_cinco_modulos_do_sistema_na_ordem_do_menu() -> None:
    assert [m.rotulo for m in MODULOS] == [
        "Atendimento",
        "Financeiro",
        "SESMT",
        "Configurações",
        "Execuções",
    ]
    assert {m.chave for m in MODULOS} == TODOS_OS_MODULOS


def test_modulos_validos_ignora_desconhecidos_e_repetidos_e_ordena() -> None:
    assert modulos_validos(["execucoes", "x", "atendimento", "execucoes"]) == (
        "atendimento",
        "execucoes",
    )
    assert modulos_validos([]) == ()


def test_acesso_por_modulo_e_administrador_com_tudo() -> None:
    comum = Acesso("ana", "Ana", frozenset({"financeiro"}))
    assert comum.pode("financeiro") and not comum.pode(CONFIGURACOES)
    assert [m.chave for m in comum.permitidos()] == ["financeiro"]
    admin = Acesso("admin", "admin", frozenset(), administrador=True)
    assert all(admin.pode(m.chave) for m in MODULOS)
    assert len(admin.permitidos()) == len(MODULOS)


@pytest.mark.parametrize("usuario", ["ana", "ana.silva", "a-b_c9", "123", "x" * 40])
def test_usuarios_validos(usuario: str) -> None:
    assert validar_usuario(usuario) == usuario


def test_usuario_e_normalizado_para_minusculas() -> None:
    assert validar_usuario("  Ana.Silva ") == "ana.silva"
    assert normalizar_usuario(" ADMIN ") == "admin"


@pytest.mark.parametrize("usuario", ["", "ab", "x" * 41, "ana silva", "ana@x.com", "-ana", "ána"])
def test_usuarios_invalidos(usuario: str) -> None:
    with pytest.raises(ValueError, match="O usuário deve ter"):
        validar_usuario(usuario)


def test_nome_e_senha() -> None:
    assert validar_nome("  Ana   Maria  Silva ") == "Ana Maria Silva"
    with pytest.raises(ValueError, match="Informe o nome"):
        validar_nome(" a ")
    with pytest.raises(ValueError, match="no máximo 120"):
        validar_nome("x" * 121)
    assert validar_senha("12345678") == "12345678"
    with pytest.raises(ValueError, match="ao menos 8"):
        validar_senha("1234567")
    with pytest.raises(ValueError, match="no máximo 72"):
        validar_senha("x" * 73)
    with pytest.raises(ValueError, match="no máximo 72"):
        validar_senha("é" * 40)  # 80 bytes: o bcrypt cortaria a senha em silêncio


def test_repositorio_em_memoria_cria_atualiza_e_exclui() -> None:
    with UoWEmMemoria(BancoEmMemoria()) as u:
        ana = u.usuarios.criar("Ana", "Ana", "hash", ["financeiro"])
        assert ana.usuario == "ana" and ana.permissoes == ("financeiro",)
        with pytest.raises(UsuarioDuplicado):
            u.usuarios.criar("Outra Ana", "ANA", "hash", [])
        bia = u.usuarios.criar("Bia", "bia", "hash", [])
        u.usuarios.atualizar(bia.id, "Beatriz", "beatriz", ["sesmt"])
        atual = u.usuarios.obter(bia.id)
        assert atual is not None
        assert (atual.nome, atual.usuario, atual.senha_hash, atual.permissoes) == (
            "Beatriz",
            "beatriz",
            "hash",  # senha mantida
            ("sesmt",),
        )
        u.usuarios.atualizar(bia.id, "Beatriz", "beatriz", ["sesmt"], senha_hash="novo")
        assert u.usuarios.obter_por_usuario("Beatriz").senha_hash == "novo"  # type: ignore[union-attr]
        with pytest.raises(UsuarioDuplicado):
            u.usuarios.atualizar(bia.id, "Beatriz", "ana", [])
        assert [x.nome for x in u.usuarios.listar()] == ["Ana", "Beatriz"]
        assert u.usuarios.excluir(ana.id) is True and u.usuarios.excluir(ana.id) is False
        assert u.usuarios.obter_por_usuario("ana") is None
