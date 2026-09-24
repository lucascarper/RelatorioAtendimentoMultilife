"""Alertas para o e-mail técnico (falhas de coleta, consolidação e envio)."""

from __future__ import annotations

from collections.abc import Mapping

import structlog

from relatorio.application.configuracao import CHAVE_EMAIL_TECNICO, ConfiguracaoRelatorio
from relatorio.application.ports import EnviadorEmail, FabricaUoW, RenderizadorEmail

log = structlog.get_logger(__name__)


class AlertarTecnico:
    def __init__(
        self,
        uow: FabricaUoW,
        email: EnviadorEmail,
        renderizador: RenderizadorEmail,
        configuracao_padrao: ConfiguracaoRelatorio,
    ) -> None:
        self._uow = uow
        self._email = email
        self._renderizador = renderizador
        self._padrao = configuracao_padrao

    def _destino(self) -> str:
        # Lê só a chave do e-mail: uma configuração de turno inválida (ou o banco fora
        # do ar) não pode desviar o alerta para outro endereço.
        try:
            with self._uow() as uow:
                valor = uow.configuracoes.obter_todas().get(CHAVE_EMAIL_TECNICO, "")
        except Exception:
            valor = ""
        return valor.strip() or self._padrao.email_alerta_tecnico

    def enviar(self, titulo: str, mensagem: str, detalhes: Mapping[str, str]) -> bool:
        """Nunca lança exceção: um alerta que falha é registrado no log e segue o fluxo."""
        destino = self._destino()
        try:
            conteudo = self._renderizador.alerta(titulo, mensagem, detalhes)
            self._email.enviar([destino], conteudo)
        except Exception:
            log.exception("alerta_tecnico_falhou", titulo=titulo)
            return False
        log.warning("alerta_tecnico_enviado", titulo=titulo, destino=destino)
        return True
