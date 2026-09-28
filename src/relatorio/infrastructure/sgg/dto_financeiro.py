"""Mapeamento dos endpoints financeiros do SGG → entidades do relatório financeiro.

Endpoints: ``contasReceber/``, ``contasPagar/``, ``contratoCliente/`` e
``fornecedor-valores/``. Os nomes de campo seguem as respostas reais da API, que em
alguns pontos diferem da documentação (ex.: ``valor_a_cobrar``/``valor_a_pagar`` na
tabela de preços, ``centro_de_custos`` como lista de rateio nas contas).

LGPD: de cada conta só ficam valores, datas, situação, classificação, rateio e o nome
do cliente pessoa jurídica. Quando o CPF/CNPJ é de pessoa física, o nome é trocado por
um identificador; descrição, links e documentos são descartados.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from relatorio.domain.financeiro import Contrato, ItemFaturado, PrecoFornecedor, Titulo
from relatorio.infrastructure.sgg.dto import _data, _inteiro, _texto

_NAO_NUMERICO = re.compile(r"[^\d,.\-]")
_SITUACAO_CANCELADA = "cancelada"


def para_decimal(valor: Any) -> Decimal | None:
    """Aceita "12.00", "15,00", "1.234,56", "R$65.00" e números; vazio vira None."""
    if valor is None:
        return None
    if isinstance(valor, int | float | Decimal):
        return Decimal(str(valor))
    texto = _NAO_NUMERICO.sub("", str(valor))
    if not texto or texto in {"-", ",", "."}:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _pessoa_fisica(documento: Any) -> bool:
    return len(re.sub(r"\D", "", str(documento or ""))) == 11


def _rateio(bruto: Any, pago: bool) -> tuple[tuple[str, Decimal], ...]:
    if not isinstance(bruto, list):
        return ()
    partes = []
    for parte in bruto:
        if not isinstance(parte, Mapping):
            continue
        centro = _texto(parte.get("centro_de_custo"))
        valor = para_decimal(parte.get("valor_pago")) if pago else None
        if not valor:
            valor = para_decimal(parte.get("valor"))
        if centro and valor is not None:
            partes.append((centro, valor))
    return tuple(partes)


def _itens(faturamento: Any) -> tuple[ItemFaturado, ...]:
    simplificado = faturamento.get("simplificado") if isinstance(faturamento, Mapping) else None
    if not isinstance(simplificado, list):
        return ()
    itens = []
    for bruto in simplificado:
        if not isinstance(bruto, Mapping):
            continue
        itens.append(
            ItemFaturado(
                id_servico=_inteiro(bruto.get("id_servico")),
                servico=_texto(bruto.get("servico")) or "Serviço sem nome",
                qtde=_inteiro(bruto.get("qtde")) or 0,
                valor=para_decimal(bruto.get("valor")) or Decimal("0"),
            )
        )
    return tuple(itens)


def para_titulo(item: Mapping[str, Any]) -> Titulo:
    """Converte uma conta a receber ou a pagar. Lança ``ValueError`` se inválida."""
    id_titulo = _inteiro(item.get("id"))
    valor = para_decimal(item.get("valor"))
    if id_titulo is None or valor is None:
        raise ValueError("conta sem id ou valor")
    situacao = _texto(item.get("situacao")) or ""
    pagamento = _data(item.get("data_pagamento"))
    id_cliente = _inteiro(item.get("id_cliente"))
    documento = item.get("cnpj_cpf_empresa", item.get("cnpj_cpf"))
    nome = _texto(item.get("nome")) or ""
    if _pessoa_fisica(documento):
        nome = f"Pessoa física #{id_cliente}" if id_cliente else "Pessoa física"
    return Titulo(
        id=id_titulo,
        valor=valor,
        situacao=situacao,
        cancelada=str(item.get("cancelada") or "0").strip() == "1"
        or situacao.casefold() == _SITUACAO_CANCELADA,
        emissao=_data(item.get("data_emissao")),
        vencimento=_data(item.get("data_vencimento")),
        pagamento=pagamento,
        valor_cobrado=para_decimal(item.get("valor_cobrado")),
        id_cliente=id_cliente,
        nome=nome,
        classificacao=_texto(item.get("centro_de_resultados")) or "",
        rateio=_rateio(item.get("centro_de_custos"), pago=pagamento is not None),
        itens=_itens(item.get("faturamento")),
    )


def para_contrato(item: Mapping[str, Any]) -> Contrato:
    id_contrato = _inteiro(item.get("id"))
    if id_contrato is None:
        raise ValueError("contrato sem id")
    faturamento = item.get("faturamento") if isinstance(item.get("faturamento"), Mapping) else {}
    excedente = faturamento.get("valor_excedente") if isinstance(faturamento, Mapping) else None
    valor_excedente = (
        para_decimal(excedente.get("valor_excedente")) if isinstance(excedente, Mapping) else None
    )
    return Contrato(
        id=id_contrato,
        id_cliente=_inteiro(item.get("id_cliente")),
        vencimento=_data(item.get("data_vencimento")),
        situacao=_texto(item.get("situacao_contrato")) or "",
        excedente_configurado=bool(valor_excedente and valor_excedente > 0),
    )


def para_preco(item: Mapping[str, Any], id_servico: int) -> PrecoFornecedor:
    cobrar = para_decimal(item.get("valor_a_cobrar", item.get("valor_cobrar")))
    pagar = para_decimal(item.get("valor_a_pagar", item.get("valor_pagar")))
    return PrecoFornecedor(
        id_servico=id_servico,
        id_fornecedor=_inteiro(item.get("id_fornecedor")),
        id_empresa=_inteiro(item.get("id_empresa")),
        valor_cobrar=cobrar or Decimal("0"),
        valor_pagar=pagar or Decimal("0"),
    )
