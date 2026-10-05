"""Módulos do Sistema de Relatórios e regras de acesso dos usuários.

O acesso é por módulo: quem tem a permissão vê e usa o módulo inteiro. O administrador do
deploy (``ADMIN_USER``) sempre tem todos os módulos e não é guardado no banco, para que
ninguém perca o acesso ao sistema por engano.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Modulo:
    chave: str
    rotulo: str
    descricao: str


ATENDIMENTO = "atendimento"
FINANCEIRO = "financeiro"
SESMT = "sesmt"
CONFIGURACOES = "configuracoes"
EXECUCOES = "execucoes"

MODULOS = (
    Modulo(
        ATENDIMENTO,
        "Atendimento",
        "Painel, destinatários, monitor ao vivo e agendas do relatório de atendimentos.",
    ),
    Modulo(FINANCEIRO, "Financeiro", "Relatório financeiro diário e seus destinatários."),
    Modulo(SESMT, "SESMT", "Relatório de gestão do SESMT e seus destinatários."),
    Modulo(
        CONFIGURACOES,
        "Configurações",
        "Regras do relatório e gerenciamento de usuários e permissões.",
    ),
    Modulo(EXECUCOES, "Execuções", "Histórico das execuções dos jobs."),
)
ROTULO_MODULO = {m.chave: m.rotulo for m in MODULOS}
TODOS_OS_MODULOS = frozenset(ROTULO_MODULO)


def modulos_validos(chaves: Iterable[str]) -> tuple[str, ...]:
    """Só as chaves que existem, sem repetição e na ordem em que os módulos aparecem."""
    pedidas = set(chaves)
    return tuple(m.chave for m in MODULOS if m.chave in pedidas)


@dataclass(frozen=True, slots=True)
class Acesso:
    """Quem está logado e a quais módulos tem acesso."""

    login: str
    nome: str
    modulos: frozenset[str]
    administrador: bool = False  # o do deploy: acesso total, não editável pelo painel

    def pode(self, modulo: str) -> bool:
        return self.administrador or modulo in self.modulos

    def permitidos(self) -> tuple[Modulo, ...]:
        return tuple(m for m in MODULOS if self.pode(m.chave))


# --------------------------------------------------------------------------- cadastro

NOME_MAX = 120
USUARIO_MIN, USUARIO_MAX = 3, 40
SENHA_MIN, SENHA_MAX = 8, 72  # o bcrypt só considera os 72 primeiros bytes
_USUARIO = re.compile(rf"^[a-z0-9][a-z0-9._-]{{{USUARIO_MIN - 1},{USUARIO_MAX - 1}}}$")


def normalizar_usuario(texto: str) -> str:
    return texto.strip().lower()


def validar_nome(texto: str) -> str:
    nome = " ".join(texto.split())
    if len(nome) < 2:
        raise ValueError("Informe o nome do usuário.")
    if len(nome) > NOME_MAX:
        raise ValueError(f"O nome pode ter no máximo {NOME_MAX} caracteres.")
    return nome


def validar_usuario(texto: str) -> str:
    usuario = normalizar_usuario(texto)
    if not _USUARIO.match(usuario):
        raise ValueError(
            f"O usuário deve ter de {USUARIO_MIN} a {USUARIO_MAX} caracteres: letras minúsculas,"
            " números, ponto, hífen ou sublinhado, começando por letra ou número."
        )
    return usuario


def validar_senha(senha: str) -> str:
    if len(senha) < SENHA_MIN:
        raise ValueError(f"A senha deve ter ao menos {SENHA_MIN} caracteres.")
    if len(senha.encode()) > SENHA_MAX:
        raise ValueError(f"A senha pode ter no máximo {SENHA_MAX} caracteres.")
    return senha
