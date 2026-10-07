"""Paginação padrão das listas do admin: tamanho da página (10, 25, 50 ou 100) e navegação.

Cada lista tem uma ``chave`` e usa os parâmetros ``<chave>_p`` (página) e ``<chave>_n``
(quantos por página) na URL, para que várias listas da mesma tela não se misturem. O rodapé
(``templates/admin/_paginacao.html``) só aparece quando a lista passa do menor tamanho.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import Request

TAMANHOS = (10, 25, 50, 100)
PADRAO = 25


@dataclass(frozen=True, slots=True)
class Pagina:
    itens: Sequence[object]
    total: int
    pagina: int
    tamanho: int
    chave: str
    caminho: str
    outros: tuple[tuple[str, str], ...]  # demais parâmetros da URL, preservados nos links

    @property
    def paginas(self) -> int:
        return max(1, -(-self.total // self.tamanho))

    @property
    def visivel(self) -> bool:
        return self.total > TAMANHOS[0]

    @property
    def primeiro(self) -> int:
        return 0 if self.total == 0 else (self.pagina - 1) * self.tamanho + 1

    @property
    def ultimo(self) -> int:
        return min(self.total, self.pagina * self.tamanho)

    @property
    def tamanhos(self) -> tuple[int, ...]:
        return TAMANHOS

    @property
    def parametro_tamanho(self) -> str:
        return f"{self.chave}_n"

    def url(self, pagina: int, tamanho: int | None = None) -> str:
        parametros = list(self.outros)
        n = tamanho or self.tamanho
        if n != PADRAO:
            parametros.append((f"{self.chave}_n", str(n)))
        if pagina > 1:
            parametros.append((f"{self.chave}_p", str(pagina)))
        consulta = f"?{urlencode(parametros)}" if parametros else ""
        return f"{self.caminho}{consulta}#{self.chave}"

    def numeros(self) -> list[int | None]:
        """Números de página a mostrar; ``None`` vira reticências."""
        todas = range(1, self.paginas + 1)
        mostrar = {p for p in todas if p in (1, self.paginas) or abs(p - self.pagina) <= 1}
        saida: list[int | None] = []
        anterior = 0
        for p in sorted(mostrar):
            if p - anterior > 1:
                saida.append(None)
            saida.append(p)
            anterior = p
        return saida


_PARAMETRO = re.compile(r"^[a-z_]+_[np]$")


def _inteiro(texto: str | None, padrao: int) -> int:
    # Só dígitos ASCII: nada de sinal, espaço, notação científica ou dígitos de outros alfabetos.
    if texto is None or not (texto.isascii() and texto.isdigit()) or len(texto) > 6:
        return padrao
    return int(texto)


def voltar_para(request: Request, url: str) -> str:
    """``url`` com a página e o tamanho das listas que o usuário estava vendo (do Referer).

    Depois de adicionar ou excluir um item, a lista continua onde estava, em vez de voltar
    à primeira página com o tamanho padrão.
    """
    caminho, _, ancora = url.partition("#")
    referencia = urlsplit(request.headers.get("referer", ""))
    if referencia.path != caminho or "?" in caminho:
        return url
    mantidos = [
        (k, v) for k, v in parse_qsl(referencia.query) if _PARAMETRO.match(k) and _inteiro(v, 0)
    ]
    consulta = f"?{urlencode(mantidos)}" if mantidos else ""
    return f"{caminho}{consulta}{'#' + ancora if ancora else ''}"


def paginar(request: Request, itens: Sequence[object], chave: str) -> Pagina:
    consulta = request.query_params
    tamanho = _inteiro(consulta.get(f"{chave}_n"), PADRAO)
    if tamanho not in TAMANHOS:
        tamanho = PADRAO
    total = len(itens)
    ultima = max(1, -(-total // tamanho))
    pagina = min(max(1, _inteiro(consulta.get(f"{chave}_p"), 1)), ultima)
    # Só os parâmetros de paginação das outras listas da tela são levados nos links.
    outros = tuple(
        (k, v)
        for k, v in consulta.multi_items()
        if _PARAMETRO.match(k) and _inteiro(v, 0) and k not in (f"{chave}_p", f"{chave}_n")
    )
    recorte = itens[(pagina - 1) * tamanho : pagina * tamanho]
    return Pagina(recorte, total, pagina, tamanho, chave, request.url.path, outros)
