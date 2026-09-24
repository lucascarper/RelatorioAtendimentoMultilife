"""Envio de e-mail: SMTP da KingHost (produção) ou arquivo local (desenvolvimento).

A mensagem é ``multipart/alternative`` (texto puro + HTML) com a logo anexada como
imagem inline (CID) — o formato que Outlook e Gmail exibem sem bloquear a imagem.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

import structlog

from relatorio.application.modelos import ConteudoEmail
from relatorio.application.ports import ErroIntegracao

log = structlog.get_logger(__name__)


class ErroEmail(ErroIntegracao):
    pass


def montar_mensagem(
    remetente: str, remetente_nome: str, destinatarios: Sequence[str], conteudo: ConteudoEmail
) -> EmailMessage:
    mensagem = EmailMessage()
    mensagem["Subject"] = conteudo.assunto
    mensagem["From"] = formataddr((remetente_nome, remetente))
    mensagem["To"] = ", ".join(destinatarios)
    mensagem["Date"] = formatdate(localtime=True)
    mensagem["Message-ID"] = make_msgid(domain=remetente.rsplit("@", 1)[-1])
    mensagem.set_content(conteudo.texto)
    mensagem.add_alternative(conteudo.html, subtype="html")
    parte_html = next(
        (p for p in mensagem.iter_parts() if p.get_content_type() == "text/html"), None
    )
    if parte_html is None:  # pragma: no cover - add_alternative sempre cria a parte HTML
        return mensagem
    for imagem in conteudo.imagens:
        parte_html.add_related(
            imagem.conteudo,
            maintype="image",
            subtype=imagem.subtipo,
            cid=f"<{imagem.cid}>",
            filename=f"{imagem.cid}.{imagem.subtipo}",
            disposition="inline",
        )
    return mensagem


class EnviadorSmtp:
    def __init__(
        self,
        host: str,
        porta: int,
        usuario: str,
        senha: str,
        remetente: str,
        remetente_nome: str,
        *,
        usar_ssl: bool,
        timeout_s: float = 30.0,
    ) -> None:
        self._host = host
        self._porta = porta
        self._usuario = usuario
        self._senha = senha
        self._remetente = remetente
        self._remetente_nome = remetente_nome
        self._usar_ssl = usar_ssl
        self._timeout = timeout_s

    def enviar(self, destinatarios: Sequence[str], conteudo: ConteudoEmail) -> None:
        mensagem = montar_mensagem(self._remetente, self._remetente_nome, destinatarios, conteudo)
        contexto = ssl.create_default_context()
        try:
            if self._usar_ssl:
                with smtplib.SMTP_SSL(
                    self._host, self._porta, context=contexto, timeout=self._timeout
                ) as smtp:
                    smtp.login(self._usuario, self._senha)
                    smtp.send_message(mensagem)
            else:
                with smtplib.SMTP(self._host, self._porta, timeout=self._timeout) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=contexto)
                    smtp.ehlo()
                    smtp.login(self._usuario, self._senha)
                    smtp.send_message(mensagem)
        except (smtplib.SMTPException, OSError) as erro:
            # A mensagem do erro nunca inclui a senha; o host e a porta ajudam no diagnóstico.
            raise ErroEmail(
                f"Falha SMTP em {self._host}:{self._porta}: {type(erro).__name__}: {erro}"
            ) from erro
        log.info("email_enviado", assunto=conteudo.assunto, destinatarios=len(destinatarios))


class EnviadorArquivo:
    """Desenvolvimento/homologação local: grava o .eml e o .html em vez de enviar."""

    def __init__(self, pasta: Path, remetente: str, remetente_nome: str) -> None:
        self._pasta = pasta
        self._remetente = remetente
        self._remetente_nome = remetente_nome

    def enviar(self, destinatarios: Sequence[str], conteudo: ConteudoEmail) -> None:
        self._pasta.mkdir(parents=True, exist_ok=True)
        mensagem = montar_mensagem(self._remetente, self._remetente_nome, destinatarios, conteudo)
        base = self._pasta / make_msgid().strip("<>").split("@", 1)[0]
        base.with_suffix(".eml").write_bytes(bytes(mensagem))
        base.with_suffix(".html").write_text(conteudo.html, encoding="utf-8")
        log.info("email_gravado_em_arquivo", arquivo=str(base.with_suffix(".eml")))
