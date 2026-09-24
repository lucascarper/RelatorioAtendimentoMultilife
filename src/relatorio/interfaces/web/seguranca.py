"""Segurança do admin: senha bcrypt, bloqueio após 5 tentativas e CSRF (seção 15)."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import bcrypt
from fastapi import HTTPException, Request, status

MAX_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 15 * 60
CHAVE_USUARIO = "usuario"
CHAVE_CSRF = "csrf"
# Hash de referência para que usuário inexistente custe o mesmo tempo que senha errada.
_HASH_FICTICIO = bcrypt.hashpw(b"senha-ficticia", bcrypt.gensalt(rounds=12))


def gerar_hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt(rounds=12)).decode()


def conferir_credenciais(usuario: str, senha: str, usuario_ok: str, hash_ok: str) -> bool:
    usuario_confere = hmac.compare_digest(usuario.encode(), usuario_ok.encode())
    try:
        alvo = hash_ok.encode() if hash_ok else _HASH_FICTICIO
        senha_confere = bcrypt.checkpw(senha.encode(), alvo)
    except ValueError:  # hash malformado na variável de ambiente
        senha_confere = False
    return usuario_confere and senha_confere and bool(hash_ok)


@dataclass
class ControleTentativas:
    """Bloqueia por 15 min após 5 falhas seguidas do mesmo IP + usuário.

    Fica em memória: o serviço web roda com 1 réplica (seção 14). Um reinício limpa os
    contadores, o que é aceitável para um admin interno.
    """

    relogio: Callable[[], float] = time.monotonic
    _falhas: dict[str, list[float]] = field(default_factory=dict)
    _trava: threading.Lock = field(default_factory=threading.Lock)

    def bloqueado(self, chave: str) -> bool:
        with self._trava:
            falhas = [t for t in self._falhas.get(chave, []) if self._valida(t)]
            self._falhas[chave] = falhas
            return len(falhas) >= MAX_TENTATIVAS

    def registrar_falha(self, chave: str) -> int:
        with self._trava:
            falhas = [t for t in self._falhas.get(chave, []) if self._valida(t)]
            falhas.append(self.relogio())
            self._falhas[chave] = falhas
            return MAX_TENTATIVAS - len(falhas)

    def limpar(self, chave: str) -> None:
        with self._trava:
            self._falhas.pop(chave, None)

    def _valida(self, instante: float) -> bool:
        return self.relogio() - instante < BLOQUEIO_SEGUNDOS


def token_csrf(request: Request) -> str:
    token = request.session.get(CHAVE_CSRF)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CHAVE_CSRF] = token
    return str(token)


def validar_csrf(request: Request, enviado: str | None) -> None:
    esperado = request.session.get(CHAVE_CSRF)
    recebido = enviado or request.headers.get("X-CSRF-Token", "")
    if not esperado or not hmac.compare_digest(str(esperado), recebido):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sessão expirada. Recarregue a página.")


class NaoAutenticado(Exception):
    """Levada ao handler que redireciona para o login."""
