"""Erros do cliente SGG (todos são ``ErroIntegracao`` para a aplicação)."""

from __future__ import annotations

from relatorio.application.ports import ErroIntegracao


class ErroSgg(ErroIntegracao):
    def __init__(self, mensagem: str, codigo: str | None = None) -> None:
        super().__init__(f"{codigo}: {mensagem}" if codigo else mensagem)
        self.codigo = codigo


class ErroSggConfiguracao(ErroSgg):
    """Chave de API ausente ou recusada (A000/A001/S002/S006)."""


class ErroSggLimite(ErroSgg):
    """HTTP 429 — limite de requisições da API atingido."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Limite de requisições do SGG atingido (HTTP 429)", "429")
        self.retry_after = retry_after


class ErroSggServidor(ErroSgg):
    """Erros internos do SGG (S0xx/E000/HTTP 5xx) ou falha de rede: pula o ciclo."""


class ErroSggRequisicao(ErroSgg):
    """Requisição recusada pela API (códigos D/AG…): indica bug ou filtro inválido."""
