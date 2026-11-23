"""Leitura das planilhas "VENCIMENTO(s) DE PGR/PCMSO/LTCAT(s)" exportadas do SGG.

O SESMT exporta as três do SGG toda semana e envia no admin (ADR 0021). Cada planilha tem
uma linha por empresa com o documento corrente. Ficam só o código, o nome, o CNPJ, o grupo
e a validade: e-mails, telefones e emissor são descartados na leitura. Empresa com CPF
vira "Empresa #código" (LGPD, como na API).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

from relatorio.domain.sesmt import TIPOS_DOCUMENTO, DocumentoSst

MAX_LINHAS = 20_000
_TIPO = re.compile(r"\bDE\s+(" + "|".join(TIPOS_DOCUMENTO) + r")\b", re.IGNORECASE)
_LINHAS_ATE_O_CABECALHO = 10


class PlanilhaInvalida(ValueError):
    """O arquivo não é uma planilha de vencimentos do SGG que dê para ler."""


@dataclass(frozen=True, slots=True)
class PlanilhaVencimentos:
    tipo: str
    documentos: list[DocumentoSst]
    ignoradas: int  # linhas sem código ou com data de validade ilegível


def _texto(valor: Any) -> str:
    return str(valor).strip() if valor is not None else ""


def _validade(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        dia, mes, ano = (int(p) for p in _texto(valor).split("/"))
        return date(ano, mes, dia)
    except ValueError:  # vazio, outro formato ou data impossível
        return None


def _codigo(valor: Any) -> int | None:
    texto = _texto(valor)
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    return int(texto) if texto.isdigit() else None


def _empresa(id_empresa: int, nome: str, documento: str) -> tuple[str, str]:
    digitos = re.sub(r"\D", "", documento)
    if len(digitos) == 11 and int(digitos) > 0:  # CPF: pessoa física
        return f"Empresa #{id_empresa}", ""
    cnpj = documento if len(digitos) == 14 and int(digitos) > 0 else ""
    return nome or f"Empresa #{id_empresa}", cnpj


def ler_planilha_vencimentos(conteudo: bytes) -> PlanilhaVencimentos:
    try:
        planilha = load_workbook(BytesIO(conteudo), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as erro:
        raise PlanilhaInvalida("O arquivo não é uma planilha .xlsx.") from erro
    try:
        aba = planilha.worksheets[0]
        linhas = aba.iter_rows(values_only=True)
        tipo, cabecalho = _TIPO.search(aba.title), None
        for _ in range(_LINHAS_ATE_O_CABECALHO):
            linha = next(linhas, None)
            if linha is None:
                break
            textos = [_texto(c) for c in linha]
            if tipo is None:
                tipo = next(filter(None, (_TIPO.search(t) for t in textos)), None)
            if "Código Empresa" in textos:
                cabecalho = {nome: i for i, nome in enumerate(textos) if nome}
                break
        if tipo is None:
            raise PlanilhaInvalida(
                "Não deu para saber se a planilha é de PGR, PCMSO ou LTCAT: use a exportação"
                ' "Vencimento(s) de ..." do SGG, sem renomear a aba.'
            )
        if cabecalho is None or not {"Código Empresa", "Data Validade"} <= cabecalho.keys():
            raise PlanilhaInvalida(
                'A planilha precisa das colunas "Código Empresa" e "Data Validade".'
            )
        documentos: list[DocumentoSst] = []
        ignoradas = 0
        for linha in linhas:
            if len(documentos) + ignoradas >= MAX_LINHAS:
                raise PlanilhaInvalida(f"A planilha passa de {MAX_LINHAS} linhas.")
            valores = {nome: linha[i] for nome, i in cabecalho.items() if i < len(linha)}
            id_empresa = _codigo(valores.get("Código Empresa"))
            validade = _validade(valores.get("Data Validade"))
            if id_empresa is None or validade is None:
                ignoradas += any(c is not None for c in linha)
                continue
            nome, cnpj = _empresa(
                id_empresa, _texto(valores.get("Empresa")), _texto(valores.get("CNPJ/CPF"))
            )
            documentos.append(
                DocumentoSst(
                    id_empresa,
                    tipo.group(1).upper(),
                    validade,
                    empresa=nome,
                    cnpj=cnpj,
                    grupo=_texto(valores.get("Grupo")),
                )
            )
        return PlanilhaVencimentos(tipo.group(1).upper(), documentos, ignoradas)
    finally:
        planilha.close()
