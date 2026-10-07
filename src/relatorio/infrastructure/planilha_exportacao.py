"""Escreve a planilha de exportação (.xlsx) com um estilo simples e consistente.

Uma aba de capa ("Sobre") com o relatório, o período e as observações; depois as abas do
relatório e as abas de dados fonte (marcadas com o prefixo "Fonte"). Cabeçalho azul da marca,
primeira linha congelada, filtro em todas as colunas e formatos brasileiros de número e data.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from relatorio.application.ports import Relogio
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.exportacao import Aba, Coluna, Planilha, Valor

AZUL = "164F95"
AZUL_SUAVE = "EAF1FA"
CINZA = "5F6B7A"
VERDE_SUAVE = "E8F5EC"
FORMATOS = {
    "inteiro": "#,##0",
    "decimal": "#,##0.00",
    "moeda": '"R$" #,##0.00;[Red]-"R$" #,##0.00',
    "percentual": "0.0%",
    "data": "DD/MM/YYYY",
    "datahora": "DD/MM/YYYY HH:MM",
    "duracao": "[h]:mm:ss",
}
_PROIBIDOS = re.compile(r"[\[\]:*?/\\]")
LINHA_CABECALHO = 3


def _valor(valor: Valor, coluna: Coluna) -> object:
    if valor is None:
        return None
    if coluna.formato == "duracao" and isinstance(valor, int | float):
        return timedelta(seconds=round(valor))
    if isinstance(valor, datetime) and valor.tzinfo is not None:
        return valor.replace(tzinfo=None)  # o Excel não guarda fuso; já vem em Brasília
    return valor


def _nome_aba(nome: str, usados: set[str]) -> str:
    base = _PROIBIDOS.sub(" ", nome).strip()[:31] or "Aba"
    candidato, n = base, 2
    while candidato.casefold() in usados:
        sufixo = f" ({n})"
        candidato, n = base[: 31 - len(sufixo)] + sufixo, n + 1
    usados.add(candidato.casefold())
    return candidato


def _escrever_aba(aba_xlsx: Worksheet, aba: Aba) -> None:
    aba_xlsx.append([aba.titulo])
    aba_xlsx["A1"].font = Font(bold=True, size=13, color=AZUL)
    aba_xlsx.append([aba.nota or ("Dados fonte usados no relatório." if aba.fonte else "")])
    aba_xlsx["A2"].font = Font(italic=True, size=9, color=CINZA)
    aba_xlsx.append([c.nome for c in aba.colunas])
    borda = Border(bottom=Side(style="thin", color="C5CFDC"))
    for indice, coluna in enumerate(aba.colunas, start=1):
        celula = aba_xlsx.cell(row=LINHA_CABECALHO, column=indice)
        celula.font = Font(bold=True, color=AZUL)
        celula.fill = PatternFill("solid", fgColor=VERDE_SUAVE if aba.fonte else AZUL_SUAVE)
        celula.border = borda
        celula.alignment = Alignment(vertical="center", wrap_text=True)
        aba_xlsx.column_dimensions[get_column_letter(indice)].width = coluna.largura
    for linha in aba.linhas:
        aba_xlsx.append([_valor(v, c) for v, c in zip(linha, aba.colunas, strict=True)])
        numero = aba_xlsx.max_row
        for indice, coluna in enumerate(aba.colunas, start=1):
            if coluna.formato in FORMATOS:
                aba_xlsx.cell(row=numero, column=indice).number_format = FORMATOS[coluna.formato]
    if not aba.linhas:
        aba_xlsx.append(["Sem dados no período."])
        aba_xlsx.cell(row=aba_xlsx.max_row, column=1).font = Font(italic=True, color=CINZA)
    aba_xlsx.freeze_panes = f"A{LINHA_CABECALHO + 1}"
    if aba.linhas:
        ultima = get_column_letter(len(aba.colunas))
        aba_xlsx.auto_filter.ref = f"A{LINHA_CABECALHO}:{ultima}{aba_xlsx.max_row}"
    aba_xlsx.sheet_properties.tabColor = "1E7A3A" if aba.fonte else AZUL


def _capa(livro: Workbook, planilha: Planilha, gerado_em: datetime) -> None:
    capa = livro.create_sheet("Sobre", 0)
    capa.column_dimensions["A"].width = 26
    capa.column_dimensions["B"].width = 90
    capa.append([planilha.titulo])
    capa["A1"].font = Font(bold=True, size=15, color=AZUL)
    capa.append([])
    linhas: list[tuple[str, object]] = [
        ("Período", f"{planilha.inicio:%d/%m/%Y} a {planilha.fim:%d/%m/%Y}"),
        ("Gerado em", f"{gerado_em:%d/%m/%Y %H:%M}"),
        ("Abas do relatório", ", ".join(a.nome for a in planilha.abas if not a.fonte) or "-"),
        ("Abas de dados fonte", ", ".join(a.nome for a in planilha.abas if a.fonte) or "-"),
    ]
    for rotulo, valor in linhas:
        capa.append([rotulo, valor])
        capa.cell(row=capa.max_row, column=1).font = Font(bold=True, color=CINZA)
    for observacao in planilha.observacoes:
        capa.append(["Observação", observacao])
        capa.cell(row=capa.max_row, column=1).font = Font(bold=True, color=CINZA)
        capa.cell(row=capa.max_row, column=2).alignment = Alignment(wrap_text=True)
    capa.sheet_properties.tabColor = CINZA


def escrever_planilha(planilha: Planilha, gerado_em: datetime) -> bytes:
    livro = Workbook()
    padrao = livro.active
    if padrao is not None:
        livro.remove(padrao)
    usados = {"sobre"}
    for aba in sorted(planilha.abas, key=lambda a: a.fonte):  # relatório antes da fonte
        _escrever_aba(livro.create_sheet(_nome_aba(aba.nome, usados)), aba)
    _capa(livro, planilha, gerado_em)
    livro.active = 0
    saida = BytesIO()
    livro.save(saida)
    return saida.getvalue()


def nome_arquivo(tipo: str, inicio: date, fim: date) -> str:
    return f"relatorio-{tipo}-{inicio:%Y-%m-%d}-a-{fim:%Y-%m-%d}.xlsx"


class EscritorXlsx:
    def __init__(self, relogio: Relogio) -> None:
        self._relogio = relogio

    def escrever(self, planilha: Planilha) -> tuple[str, bytes]:
        agora = self._relogio.agora().astimezone(FUSO_BRASILIA)
        return (
            nome_arquivo(planilha.tipo, planilha.inicio, planilha.fim),
            escrever_planilha(planilha, agora),
        )
