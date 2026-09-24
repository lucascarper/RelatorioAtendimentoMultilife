"""Renderização dos e-mails com Jinja2 + premailer (CSS inline para Outlook e Gmail)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
from premailer import Premailer

from relatorio.application.modelos import ConteudoEmail, ImagemInline
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.email.apresentacao import montar_apresentacao

LOGO_CID = "logo-multilife"


class RenderizadorJinja:
    def __init__(self, templates_dir: Path, static_dir: Path, admin_url: str = "") -> None:
        self._env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html", "htm", "xml"], default_for_string=False),
            # Resumos antigos (outra versão de regra) não quebram a renderização.
            undefined=ChainableUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        self._logo = (static_dir / "brand" / "logo-multilife.png").read_bytes()
        self._admin_url = admin_url

    def _inline(self, html: str) -> str:
        return str(
            Premailer(
                html,
                keep_style_tags=True,  # mantém as media queries (celular)
                strip_important=False,
                remove_classes=False,
                disable_validation=True,
                cssutils_logging_level=logging.CRITICAL,
                allow_network=False,
            ).transform()
        )

    def _imagens(self) -> tuple[ImagemInline, ...]:
        return (ImagemInline(cid=LOGO_CID, conteudo=self._logo, subtipo="png"),)

    def relatorio(self, metricas: Mapping[str, Any]) -> ConteudoEmail:
        apresentacao = montar_apresentacao(metricas, self._admin_url)
        contexto = {"a": apresentacao, "logo_cid": LOGO_CID}
        html = self._env.get_template("email/resumo_diario.html").render(contexto)
        texto = self._env.get_template("email/resumo_diario.txt").render(contexto)
        return ConteudoEmail(
            assunto=apresentacao.assunto,
            html=self._inline(html),
            texto=texto.strip() + "\n",
            imagens=self._imagens(),
        )

    def alerta(self, titulo: str, mensagem: str, detalhes: Mapping[str, str]) -> ConteudoEmail:
        agora = datetime.now(tz=FUSO_BRASILIA)
        contexto = {
            "titulo": titulo,
            "mensagem": mensagem,
            "detalhes": dict(detalhes),
            "quando": f"{agora:%d/%m/%Y} às {agora:%H:%M}",
            "admin_url": self._admin_url,
            "logo_cid": LOGO_CID,
        }
        html = self._env.get_template("email/alerta.html").render(contexto)
        texto = self._env.get_template("email/alerta.txt").render(contexto)
        return ConteudoEmail(
            assunto=f"[Alerta] Relatório de Atendimentos — {titulo}",
            html=self._inline(html),
            texto=texto.strip() + "\n",
            imagens=self._imagens(),
        )
