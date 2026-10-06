"""Atendimentos por médico: os exames clínicos do dia feitos pelos médicos escolhidos.

Vem de ``GET /exames-realizados/`` (filtro ``exame=Clínico``): cada exame clínico é uma
consulta médica. Entram só os médicos marcados em Configurações; os demais são apenas
registrados (CRM e nome) para aparecerem na lista de escolha.

LGPD: o nome do trabalhador nunca é gravado. Ele é lido do SGG no momento do envio, só para
montar a planilha anexa ao e-mail, e descartado em seguida. O corpo do e-mail traz apenas
contagens por médico e por tipo de exame.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

VERSAO_REGRA_EXAMES = "1.0.0"
EXAME_CLINICO = "Clínico"
# Ordem de exibição dos tipos (a do cadastro do SGG); tipos novos vão para o fim.
TIPOS_EXAME = (
    "Admissional",
    "Periódico",
    "Demissional",
    "Mudança de Função",
    "Retorno ao Trabalho",
    "Outro",
)
_CRM = re.compile(r"^(\d{2,14})-?([A-Z]{2})$")


def normalizar_crm(texto: str) -> str:
    """Aceita "34985df" ou "34985 - DF" e devolve "34985-DF"; ``ValueError`` se não for CRM."""
    compacto = re.sub(r"\s+", "", texto or "").upper()
    achado = _CRM.match(compacto)
    if not achado:
        raise ValueError("Informe o CRM no formato número-UF, por exemplo 34985-DF.")
    return f"{achado.group(1)}-{achado.group(2)}"


@dataclass(frozen=True, slots=True)
class Medico:
    crm: str
    nome: str
    selecionado: bool = False
    visto_em: date | None = None  # último dia em que apareceu num exame clínico


@dataclass(frozen=True, slots=True)
class ExameClinico:
    id: int
    data: date
    id_empresa: int
    id_funcionario: int
    crm: str
    medico: str
    tipo: str
    empresa: str = ""
    # Só existe em memória, no envio (planilha anexa). Nunca é gravado nem registrado em log.
    funcionario: str = field(default="", repr=False, compare=False)


def ordem_tipo(tipo: str) -> tuple[int, str]:
    return (TIPOS_EXAME.index(tipo) if tipo in TIPOS_EXAME else len(TIPOS_EXAME), tipo)


def resumo_por_medico(
    exames: Iterable[ExameClinico], medicos: Sequence[Medico], dia: date
) -> dict[str, Any]:
    """Contagens do corpo do e-mail: um item por médico escolhido, mesmo sem atendimento."""
    por_medico: dict[str, Counter[str]] = {m.crm: Counter() for m in medicos}
    for exame in exames:
        if exame.crm in por_medico:
            por_medico[exame.crm][exame.tipo] += 1
    itens: list[dict[str, Any]] = [
        {
            "crm": m.crm,
            "nome": m.nome,
            "total": sum(por_medico[m.crm].values()),
            "por_tipo": [
                {"tipo": tipo, "total": total}
                for tipo, total in sorted(por_medico[m.crm].items(), key=lambda i: ordem_tipo(i[0]))
            ],
        }
        for m in medicos
    ]
    itens.sort(key=lambda i: (-int(i["total"]), str(i["nome"]).casefold()))
    return {
        "data": dia.isoformat(),
        "total": sum(i["total"] for i in itens),
        "medicos": itens,
        "versao_regra": VERSAO_REGRA_EXAMES,
    }


def linhas_da_planilha(
    exames: Iterable[ExameClinico], medicos: Sequence[Medico]
) -> list[tuple[Medico, list[ExameClinico]]]:
    """Uma aba por médico escolhido que teve atendimento, na ordem do nome."""
    por_crm: dict[str, list[ExameClinico]] = {}
    for exame in exames:
        por_crm.setdefault(exame.crm, []).append(exame)
    abas = []
    for medico in sorted(medicos, key=lambda m: m.nome.casefold()):
        linhas = por_crm.get(medico.crm)
        if linhas:
            linhas.sort(key=lambda e: (e.empresa.casefold(), e.funcionario.casefold(), e.id))
            abas.append((medico, linhas))
    return abas
