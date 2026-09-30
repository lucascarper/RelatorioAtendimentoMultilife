"""Mapeamento dos endpoints do SESMT no SGG → entidades do relatório de gestão.

Endpoints: ``empresa/``, ``contratoCliente/``, ``programasLaudos/`` e ``getEvtEsocial/``.

LGPD: da empresa só ficam código, nome, CNPJ, grupo e a situação do eSocial (quando o
documento é CPF, o nome vira "Empresa #id"). Do evento do eSocial ficam tipo, datas,
código do funcionário, recibo e protocolo: o XML, que traz o CPF do trabalhador, é lido
só para extrair a hora de geração e depois descartado.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import ContratoSesmt, DocumentoSst, EmpresaSesmt, EventoEsocial
from relatorio.infrastructure.sgg.dto import _data, _inteiro, _texto

# Id do evento no XML do eSocial: "ID", tipo de inscrição (1), CNPJ (14), AAAAMMDDHHMMSS, seq (5).
_ID_EVENTO = re.compile(r"\bId=['\"]ID\d\d{14}(\d{14})\d{5}['\"]")
_VERDADEIRO = {"sim", "s", "1", "true"}


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


def para_contrato_sesmt(item: Mapping[str, Any]) -> ContratoSesmt:
    id_contrato = _inteiro(item.get("id"))
    if id_contrato is None:
        raise ValueError("contrato sem id")
    return ContratoSesmt(
        id=id_contrato,
        id_cliente=_inteiro(item.get("id_cliente")),
        vencimento=_data(item.get("data_vencimento")),
        situacao=_texto(item.get("situacao_contrato")) or "",
        ultimo=(_texto(item.get("ultimo")) or "").casefold() in _VERDADEIRO,
        codigo=_texto(item.get("codigo_interno")) or "",
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


def hora_de_geracao(xml: Any, dia: date | None) -> datetime | None:
    """Hora em que o evento foi gerado, lida do Id do XML (só vale se bater com o dia)."""
    achado = _ID_EVENTO.search(str(xml or ""))
    if achado is None:
        return None
    try:
        instante = datetime.strptime(achado.group(1), "%Y%m%d%H%M%S").replace(tzinfo=FUSO_BRASILIA)
    except ValueError:
        return None
    return instante if dia is None or instante.date() == dia else None


def para_evento_esocial(item: Mapping[str, Any]) -> EventoEsocial:
    id_empresa = _inteiro(item.get("id_empresa"))
    tipo = _texto(item.get("tipo_evento"))
    codigo = _texto(item.get("codigo"))
    if id_empresa is None or tipo is None or codigo is None:
        raise ValueError("evento sem código, tipo ou empresa")
    geracao = _data(item.get("data_geracao"))
    return EventoEsocial(
        codigo=codigo,
        tipo=tipo,
        id_empresa=id_empresa,
        id_funcionario=_inteiro(item.get("id_funcionario")),
        data_geracao=geracao,
        gerado_em=hora_de_geracao(item.get("xml"), geracao),
        prazo=_data(item.get("prazo")),
        recibo=_texto(item.get("recibo")) or "",
        protocolo=_texto(item.get("protocolo")) or "",
    )
