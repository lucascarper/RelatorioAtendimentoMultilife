"""Gráfico de linhas da projeção (PNG gerado sem biblioteca de imagem)."""

from __future__ import annotations

import struct
import zlib

import pytest

from relatorio.infrastructure.email.grafico import (
    ALTURA,
    LARGURA,
    escala,
    grafico_projecao,
    rotulo_valor,
)


def serie(valores: list[float]) -> dict[str, object]:
    return {
        "inicio": "2026-10-26",
        "dias": len(valores) - 1,
        "entradas": valores,
        "saidas": [v / 2 for v in valores],
        "saldo": [v / 2 for v in valores],
    }


def test_png_valido_com_o_tamanho_esperado() -> None:
    png = grafico_projecao(serie([float(n * 1000) for n in range(91)]))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    largura, altura = struct.unpack(">II", png[16:24])
    assert (largura, altura) == (LARGURA, ALTURA)
    idat = png[png.index(b"IDAT") + 4 : png.rindex(b"IEND") - 8]
    assert len(zlib.decompress(idat)) == ALTURA * (1 + LARGURA * 3)


def test_serie_zerada_nao_quebra() -> None:  # sem histórico, tudo zero
    assert grafico_projecao(serie([0.0] * 91)).startswith(b"\x89PNG")


def test_serie_com_saldo_negativo_inclui_o_zero_na_escala() -> None:
    piso, teto, passo = escala(-48_000.0, 750_000.0)
    assert piso < 0 < teto and passo > 0
    assert (teto - piso) / passo == pytest.approx(round((teto - piso) / passo))


@pytest.mark.parametrize(
    ("valor", "texto"),
    [
        (0, "0"),
        (950, "950"),
        (12_500, "12,5 mil"),
        (250_000, "250 mil"),
        (2_300_000, "2,3 mi"),
        (-48_000, "-48 mil"),
    ],
)
def test_rotulos_do_eixo(valor: float, texto: str) -> None:
    assert rotulo_valor(valor) == texto
