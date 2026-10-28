"""Gráfico de linhas da projeção do caixa, como PNG (e-mail não executa SVG nem JavaScript).

Desenha direto num buffer RGB, sem biblioteca de imagem: linhas suavizadas, grade, eixos e
rótulos em uma fonte de pontos 5x7 embutida (só os caracteres que o gráfico usa). O PNG
sai ao dobro do tamanho de exibição para ficar nítido em telas de alta resolução.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Mapping
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

LARGURA, ALTURA = 1152, 420  # 2x de 576 x 210 px
MARGEM_ESQ, MARGEM_DIR, MARGEM_TOPO, MARGEM_BASE = 176, 64, 22, 54
FUNDO = (255, 255, 255)
GRADE = (227, 232, 239)
ZERO = (178, 188, 206)
TEXTO = (98, 104, 131)
ENTRADAS = (47, 74, 128)
SAIDAS = (180, 52, 50)
SALDO = (30, 122, 58)
ESCALA_TEXTO = 3

Cor = tuple[int, int, int]

_FONTE = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00001", "00010", "00100", "01000", "10000", "10000"),
    "m": ("00000", "00000", "11010", "10101", "10101", "10101", "10101"),
    "i": ("00100", "00000", "01100", "00100", "00100", "00100", "01110"),
    "l": ("01100", "00100", "00100", "00100", "00100", "00100", "01110"),
    " ": ("00000",) * 7,
}


class _Imagem:
    def __init__(self) -> None:
        self.px = bytearray(bytes(FUNDO) * (LARGURA * ALTURA))

    def misturar(self, x: int, y: int, cor: Cor, cobertura: float) -> None:
        if 0 <= x < LARGURA and 0 <= y < ALTURA and cobertura > 0:
            i = (y * LARGURA + x) * 3
            c = min(1.0, cobertura)
            for k in range(3):
                self.px[i + k] = round(self.px[i + k] * (1 - c) + cor[k] * c)

    def retangulo(self, x0: int, y0: int, x1: int, y1: int, cor: Cor) -> None:
        for y in range(max(0, y0), min(ALTURA, y1)):
            for x in range(max(0, x0), min(LARGURA, x1)):
                self.misturar(x, y, cor, 1.0)

    def segmento(
        self, a: tuple[float, float], b: tuple[float, float], cor: Cor, largura: float
    ) -> None:
        """Linha suavizada: cobertura pela distância de cada pixel ao segmento."""
        meia = largura / 2
        x0, y0, x1, y1 = a[0], a[1], b[0], b[1]
        dx, dy = x1 - x0, y1 - y0
        tamanho2 = dx * dx + dy * dy
        for y in range(int(min(y0, y1) - meia - 1), int(max(y0, y1) + meia + 2)):
            for x in range(int(min(x0, x1) - meia - 1), int(max(x0, x1) + meia + 2)):
                px, py = x + 0.5, y + 0.5
                t = (
                    0.0
                    if tamanho2 == 0
                    else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / tamanho2))
                )
                dist = math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))
                self.misturar(x, y, cor, meia + 0.5 - dist)

    def circulo(self, centro: tuple[float, float], raio: float, cor: Cor) -> None:
        cx, cy = centro
        for y in range(int(cy - raio - 1), int(cy + raio + 2)):
            for x in range(int(cx - raio - 1), int(cx + raio + 2)):
                self.misturar(x, y, cor, raio + 0.5 - math.hypot(x + 0.5 - cx, y + 0.5 - cy))

    def texto(self, x: int, y: int, texto: str, cor: Cor, alinhar: str = "esq") -> None:
        passo = 6 * ESCALA_TEXTO
        if alinhar == "dir":
            x -= len(texto) * passo - ESCALA_TEXTO
        elif alinhar == "centro":
            x -= (len(texto) * passo - ESCALA_TEXTO) // 2
        for n, letra in enumerate(texto):
            for linha, bits in enumerate(_FONTE.get(letra, _FONTE[" "])):
                for coluna, bit in enumerate(bits):
                    if bit == "1":
                        ox = x + n * passo + coluna * ESCALA_TEXTO
                        oy = y + linha * ESCALA_TEXTO
                        self.retangulo(ox, oy, ox + ESCALA_TEXTO, oy + ESCALA_TEXTO, cor)

    def png(self) -> bytes:
        linhas = b"".join(
            b"\x00" + bytes(self.px[y * LARGURA * 3 : (y + 1) * LARGURA * 3]) for y in range(ALTURA)
        )

        def bloco(tipo: bytes, dados: bytes) -> bytes:
            return (
                struct.pack(">I", len(dados))
                + tipo
                + dados
                + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + bloco(b"IHDR", struct.pack(">IIBBBBB", LARGURA, ALTURA, 8, 2, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(linhas, 9))
            + bloco(b"IEND", b"")
        )


def _passo_bonito(intervalo: float, divisoes: int) -> float:
    bruto = intervalo / divisoes if intervalo > 0 else 1.0
    base = 10.0 ** math.floor(math.log10(bruto))
    for multiplo in (1, 2, 2.5, 5, 10):
        if bruto <= multiplo * base:
            return multiplo * base
    return 10 * base


def escala(minimo: float, maximo: float, divisoes: int = 4) -> tuple[float, float, float]:
    """(piso, teto, passo) com limites em números redondos e o zero sempre dentro."""
    minimo, maximo = min(minimo, 0.0), max(maximo, 0.0)
    passo = _passo_bonito(maximo - minimo, divisoes)
    piso, teto = math.floor(minimo / passo) * passo, math.ceil(maximo / passo) * passo
    if teto <= piso:  # série toda zerada (sem histórico): eixo de 0 a 4
        teto = piso + passo * divisoes
    return piso, teto, passo


def rotulo_valor(valor: float) -> str:
    """12500 → "12,5 mil"; 2300000 → "2,3 mi"; negativos levam sinal."""
    absoluto = abs(valor)
    if absoluto >= 1_000_000:
        numero, sufixo = absoluto / 1_000_000, " mi"
    elif absoluto >= 1_000:
        numero, sufixo = absoluto / 1_000, " mil"
    else:
        numero, sufixo = absoluto, ""
    texto = f"{numero:.1f}".rstrip("0").rstrip(".") if numero < 100 else f"{numero:.0f}"
    texto = texto.replace(".", ",")
    return ("-" if valor < 0 and numero else "") + texto + sufixo


def grafico_projecao(serie: Mapping[str, Any]) -> bytes:
    """PNG com as linhas acumuladas de entradas, saídas e saldo ao longo dos 90 dias."""
    entradas: list[float] = list(serie["entradas"])
    saidas: list[float] = list(serie["saidas"])
    saldo: list[float] = list(serie["saldo"])
    dias = int(serie["dias"])
    inicio = date.fromisoformat(serie["inicio"])
    piso, teto, passo = escala(min(saldo + saidas + entradas), max(saldo + saidas + entradas))

    x0, x1 = MARGEM_ESQ, LARGURA - MARGEM_DIR
    y0, y1 = MARGEM_TOPO, ALTURA - MARGEM_BASE

    def px(n: int) -> float:
        return x0 + (x1 - x0) * n / dias

    def py(valor: float) -> float:
        return y1 - (y1 - y0) * (valor - piso) / (teto - piso)

    img = _Imagem()
    # Grade horizontal com rótulos de valor; o zero ganha uma linha mais marcada.
    quantidade = round((teto - piso) / passo)
    for k in range(quantidade + 1):
        valor = piso + k * passo
        y = round(py(valor))
        img.retangulo(x0, y - 1, x1, y + 1, ZERO if abs(valor) < passo / 1000 else GRADE)
        img.texto(x0 - 14, y - 3 * ESCALA_TEXTO - 1, rotulo_valor(valor), TEXTO, "dir")
    # Grade vertical a cada 30 dias, com a data embaixo.
    for n in range(0, dias + 1, 30):
        x = round(px(n))
        img.retangulo(x - 1, y0, x + 1, y1, GRADE)
        dia = inicio + timedelta(days=max(n - 1, 0))
        img.texto(x, y1 + 18, f"{dia:%d/%m}", TEXTO, "centro")
    # Linhas: saldo por cima, com mais espessura.
    for valores, cor, largura in (
        (entradas, ENTRADAS, 5.0),
        (saidas, SAIDAS, 5.0),
        (saldo, SALDO, 6.5),
    ):
        pontos = [(px(n), py(v)) for n, v in enumerate(valores)]
        for a, b in pairwise(pontos):
            img.segmento(a, b, cor, largura)
        img.circulo(pontos[-1], largura * 0.95, cor)
    return img.png()
