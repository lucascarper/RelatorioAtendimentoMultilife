"""Link de definição de senha: "Esqueci minha senha" e "Enviar acesso" (ADR 0019).

O token é aleatório (``secrets``), de uso único e só o SHA-256 dele vai ao banco. A validade
conta pelo relógio da aplicação, o mesmo que confere o link.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import timedelta

from relatorio.application.modelos import UsuarioSistema
from relatorio.application.ports import EnviadorEmail, FabricaUoW, Relogio, RenderizadorEmail

VALIDADE_ESQUECI = timedelta(hours=1)
VALIDADE_CONVITE = timedelta(hours=24)  # quem recebe o acesso pode demorar a ver o e-mail


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def texto_validade(validade: timedelta) -> str:
    horas = int(validade.total_seconds() // 3600)
    return "1 hora" if horas == 1 else f"{horas} horas"


class RecuperacaoSenha:
    def __init__(
        self,
        uow: FabricaUoW,
        relogio: Relogio,
        email: EnviadorEmail,
        renderizador: RenderizadorEmail,
    ) -> None:
        self._uow = uow
        self._relogio = relogio
        self._email = email
        self._renderizador = renderizador

    def enviar_link(
        self,
        usuario: UsuarioSistema,
        link_para: Callable[[str], str],
        validade: timedelta,
        *,
        convite: bool,
    ) -> None:
        """Cria o link e manda ao e-mail do usuário. Lança o erro do envio, se houver."""
        if not usuario.email:
            raise ValueError("usuário sem e-mail")
        token = secrets.token_urlsafe(32)
        agora = self._relogio.agora()
        with self._uow() as uow:
            uow.tokens_senha.criar(hash_token(token), usuario.id, agora + validade, agora)
            uow.commit()
        conteudo = self._renderizador.acesso(
            usuario.nome,
            usuario.usuario,
            link_para(token),
            texto_validade(validade),
            convite=convite,
        )
        self._email.enviar([usuario.email], conteudo)

    def dono_do_link(self, token: str) -> UsuarioSistema | None:
        with self._uow() as uow:
            id_usuario = uow.tokens_senha.valido(hash_token(token), self._relogio.agora())
            return uow.usuarios.obter(id_usuario) if id_usuario is not None else None

    def redefinir(self, token: str, senha_hash: str) -> bool:
        """Grava a nova senha e gasta o link; False se ele já foi usado ou venceu."""
        with self._uow() as uow:
            id_usuario = uow.tokens_senha.consumir(hash_token(token), self._relogio.agora())
            if id_usuario is None:
                return False
            uow.usuarios.definir_senha(id_usuario, senha_hash)
            uow.commit()
            return True
