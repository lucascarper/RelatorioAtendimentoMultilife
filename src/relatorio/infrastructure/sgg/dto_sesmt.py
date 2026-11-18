"""Mapeamento dos endpoints do SESMT no SGG → entidades do relatório de gestão.

Endpoints: ``empresa/``, ``programasLaudos/`` e ``getEvtEsocial/``.

LGPD: da empresa só ficam código, nome, CNPJ, grupo e a situação do eSocial (quando o
documento é CPF, o nome vira "Empresa #id"). Do evento do eSocial ficam código, tipo,
empresa, data de geração e recibo: o XML (com o CPF do trabalhador) e o código do
funcionário são descartados.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from relatorio.domain.sesmt import DocumentoSst, EmpresaSesmt, EventoEsocial
from relatorio.infrastructure.sgg.dto import _data, _inteiro, _texto


def _digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def para_empresa(item: Mapping[str, Any]) -> EmpresaSesmt:
    """Converte um item de ``GET /empresa/``. Lança ``ValueError`` se inválido."""
    id_empresa = _inteiro(item.get("id_empresa"))
    if id_empresa is None:
        raise ValueError("empresa sem id")
    cnpj = _digitos(item.get("CNPJ"))
    cpf = _digitos(item.get("CPF"))
    pessoa_fisica = (len(cnpj) == 11 and int(cnpj) > 0) or (len(cpf) == 11 and int(cpf) > 0)
    nome = "" if pessoa_fisica else (_texto(item.get("nome")) or _texto(item.get("fantasia")) or "")
    cnpj_valido = len(cnpj) == 14 and int(cnpj) > 0  # o SGG preenche 00.000.000/0000-00 sem CNPJ
    return EmpresaSesmt(
        id=id_empresa,
        nome=nome or f"Empresa #{id_empresa}",
        cnpj=(_texto(item.get("CNPJ")) or "") if cnpj_valido else "",
        id_grupo=_texto(item.get("id_grupo")) or "",
        esocial_habilitado=(_texto(item.get("situacao_esocial")) or "").casefold() == "habilitada",
    )


def para_documento(item: Mapping[str, Any]) -> DocumentoSst:
    """Programa ou laudo (PGR, PCMSO, LTCAT…). O link e o emissor são descartados."""
    id_empresa = _inteiro(item.get("id_empresa"))
    tipo = _texto(item.get("tipo"))
    if id_empresa is None or tipo is None:
        raise ValueError("documento sem empresa ou tipo")
    return DocumentoSst(
        id_empresa=id_empresa,
        tipo=tipo.upper(),
        vencimento=_data(item.get("data_vencimento")),
        emissao=_data(item.get("data_emissao")),
        situacao=_texto(item.get("situacao")) or "",
        codigo=_texto(item.get("codigo")) or "",
    )


def para_evento_esocial(item: Mapping[str, Any]) -> EventoEsocial:
    id_empresa = _inteiro(item.get("id_empresa"))
    tipo = _texto(item.get("tipo_evento"))
    codigo = _texto(item.get("codigo"))
    if id_empresa is None or tipo is None or codigo is None:
        raise ValueError("evento sem código, tipo ou empresa")
    return EventoEsocial(
        codigo=codigo,
        tipo=tipo,
        id_empresa=id_empresa,
        data_geracao=_data(item.get("data_geracao")),
        recibo=_texto(item.get("recibo")) or "",
    )
