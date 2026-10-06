"""Mapeamento de ``exames-realizados/`` e ``medico/`` → entidades dos atendimentos por médico.

LGPD: do exame ficam código, data, empresa, código do funcionário, CRM e nome do médico e
o tipo. O nome do trabalhador só é copiado para ``funcionario`` (campo que nunca é gravado);
CPF, data de nascimento, resultado, link do ASO e o arquivo são ignorados.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from relatorio.domain.exames import ExameClinico, Medico, normalizar_crm
from relatorio.infrastructure.sgg.dto import _data, _inteiro, _texto


def para_exame_clinico(item: Mapping[str, Any]) -> ExameClinico:
    """Converte um item de ``GET /exames-realizados/``. Lança ``ValueError`` se inválido."""
    id_exame = _inteiro(item.get("id_exames_lancados"))
    if id_exame is None:
        raise ValueError("exame sem id")
    dia = _data(item.get("data_exames_lancados"))
    if dia is None:
        raise ValueError("exame sem data")
    crm = normalizar_crm(_texto(item.get("medico")) or "")
    return ExameClinico(
        id=id_exame,
        data=dia,
        id_empresa=_inteiro(item.get("id_empresa")) or 0,
        id_funcionario=_inteiro(item.get("id_funcionario")) or 0,
        crm=crm,
        medico=_texto(item.get("nome_medico")) or crm,
        tipo=_texto(item.get("tipo_exame")) or "Outro",
        funcionario=_texto(item.get("nome_funcionario")) or "",
    )


def para_medico(item: Mapping[str, Any]) -> Medico:
    """Converte um item de ``GET /medico/``. Lança ``ValueError`` se não tiver CRM."""
    crm = normalizar_crm(_texto(item.get("CRM")) or "")
    return Medico(crm=crm, nome=_texto(item.get("nome")) or crm)
