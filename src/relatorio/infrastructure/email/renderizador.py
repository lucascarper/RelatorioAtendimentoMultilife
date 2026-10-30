"""Renderização dos e-mails com Jinja2 + premailer (CSS inline para Outlook e Gmail)."""

from __future__ import annotations

import base64
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape
from premailer import Premailer

from relatorio.application.modelos import Anexo, ConteudoEmail, ImagemInline
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.email.apresentacao import montar_apresentacao
from relatorio.infrastructure.email.apresentacao_financeira import montar_apresentacao_financeira
from relatorio.infrastructure.email.apresentacao_sesmt import montar_apresentacao_sesmt
from relatorio.infrastructure.email.grafico import grafico_projecao

LOGO_CID = "logo-multilife"
GRAFICO_CID = "grafico-projecao"

# O HTML do e-mail é uma coluna de 640 px sobre fundo cinza; no PDF ela ocupa a folha toda.
CSS_PDF = """
    @page { size: A4; margin: 10mm }
    body, table[bgcolor="#F2F4F8"], td[bgcolor="#F2F4F8"] { background: #FFF !important }
    table.container { width: 100% !important; max-width: none !important }
    table[width="100%"] { width: 100% !important }
    tr { break-inside: avoid }
    """


class _Cartao(Protocol):
    @property
    def rotulo(self) -> str: ...

    @property
    def valor(self) -> str: ...


def _resumo(manchete: str, cartoes: Sequence[_Cartao]) -> tuple[str, ...]:
    return (manchete, *(f"{c.rotulo}: {c.valor}" for c in cartoes))


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
            resumo=_resumo(apresentacao.manchete, apresentacao.kpis),
        )

    def relatorio_financeiro(self, metricas: Mapping[str, Any]) -> ConteudoEmail:
        serie = metricas.get("projecao_serie")
        apresentacao = montar_apresentacao_financeira(
            metricas, self._admin_url, GRAFICO_CID if serie else ""
        )
        contexto = {"a": apresentacao, "logo_cid": LOGO_CID}
        html = self._env.get_template("email/relatorio_financeiro.html").render(contexto)
        texto = self._env.get_template("email/relatorio_financeiro.txt").render(contexto)
        imagens = self._imagens()
        if serie:
            imagens += (ImagemInline(cid=GRAFICO_CID, conteudo=grafico_projecao(serie)),)
        return ConteudoEmail(
            assunto=apresentacao.assunto,
            html=self._inline(html),
            texto=texto.strip() + "\n",
            imagens=imagens,
            resumo=_resumo(apresentacao.manchete, apresentacao.cartoes),
        )

    def relatorio_sesmt(self, metricas: Mapping[str, Any]) -> ConteudoEmail:
        apresentacao = montar_apresentacao_sesmt(metricas, self._admin_url)
        contexto = {"a": apresentacao, "logo_cid": LOGO_CID}
        html = self._env.get_template("email/relatorio_sesmt.html").render(contexto)
        texto = self._env.get_template("email/relatorio_sesmt.txt").render(contexto)
        return ConteudoEmail(
            assunto=apresentacao.assunto,
            html=self._inline(html),
            texto=texto.strip() + "\n",
            imagens=self._imagens(),
            resumo=_resumo(apresentacao.manchete, ()),
        )

    def acesso(
        self, nome: str, usuario: str, link: str, validade: str, *, convite: bool
    ) -> ConteudoEmail:
        """Link de definição de senha: "Enviar acesso" (convite) ou "Esqueci minha senha"."""
        assunto = (
            "Seu acesso ao Sistema de Relatórios — MultiLife"
            if convite
            else "Redefinir senha — Sistema de Relatórios MultiLife"
        )
        contexto = {
            "assunto": assunto,
            "nome": nome,
            "usuario": usuario,
            "link": link,
            "validade": validade,
            "convite": convite,
            "logo_cid": LOGO_CID,
        }
        html = self._env.get_template("email/acesso.html").render(contexto)
        texto = self._env.get_template("email/acesso.txt").render(contexto)
        return ConteudoEmail(
            assunto=assunto,
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

    def pdf(self, conteudo: ConteudoEmail) -> Anexo:
        from weasyprint import CSS, HTML  # noqa: PLC0415 - exige o pango; só quem gera PDF paga

        html = conteudo.html
        for imagem in conteudo.imagens:  # o PDF não resolve ``cid:``
            dados = base64.b64encode(imagem.conteudo).decode()
            html = html.replace(f"cid:{imagem.cid}", f"data:image/{imagem.subtipo};base64,{dados}")
        pdf = HTML(string=html).write_pdf(stylesheets=[CSS(string=CSS_PDF)])
        # "Relatório Financeiro — 29/10/2026 (quinta-feira)" → Relatorio-Financeiro-29-10-2026.pdf
        base = unicodedata.normalize("NFKD", conteudo.assunto.split(" (")[0])
        nome = re.sub(r"[^A-Za-z0-9]+", "-", base.encode("ascii", "ignore").decode()).strip("-")
        return Anexo(nome=f"{nome}.pdf", conteudo=pdf, tipo="application/pdf")

    def mensagem(self, conteudo: ConteudoEmail, nome: str | None) -> ConteudoEmail:
        contexto = {"nome": nome, "resumo": conteudo.resumo, "admin_url": self._admin_url}
        html = self._env.get_template("email/mensagem.html").render(contexto)
        texto = self._env.get_template("email/mensagem.txt").render(contexto)
        return replace(conteudo, html=html, texto=texto.strip() + "\n", imagens=())
