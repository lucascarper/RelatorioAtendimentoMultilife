"""Diagnóstico dos atendimentos por médico: por que um exame do SGG não chegou ao relatório.

Somente leitura e sem dados pessoais na saída (nem nomes nem CPF): só códigos, datas, tipos e
contagens. Compara, para um dia, a leitura que o sistema faz com leituras de referência que
não dependem da paginação de 100 itens:

* leitura do sistema (filtro ``exame=Clínico``, 100 por página);
* a mesma leitura repetida e com outros tamanhos de página (a lista muda entre páginas?);
* leitura dividida por tipo de exame (cada parte cabe em uma página);
* leitura sem o filtro de exame, para achar nomes de exame clínico que o filtro não pega;
* opcionalmente, todos os exames de um funcionário no dia.

    relatorio diagnostico-exames --data 2026-08-25 --funcionario 37563
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Protocol

from relatorio.domain.exames import EXAME_CLINICO, TIPOS_EXAME
from relatorio.infrastructure.sgg.dto_exames import para_exame_clinico

Paginas = list[list[dict[str, Any]]]


class LeitorExames(Protocol):
    def exames_realizados_paginas(
        self, filtros: Mapping[str, str], tamanho: int = ...
    ) -> Paginas: ...


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold()


def _resumo(item: Mapping[str, Any]) -> dict[str, Any]:
    """Só campos sem dado pessoal."""
    return {
        "id": item.get("id_exames_lancados"),
        "data": item.get("data_exames_lancados"),
        "exame": item.get("exame"),
        "tipo_exame": item.get("tipo_exame"),
        "crm": item.get("medico"),
        "id_funcionario": item.get("id_funcionario"),
        "id_empresa": item.get("id_empresa"),
    }


def _ids(paginas: Paginas) -> list[Any]:
    return [i.get("id_exames_lancados") for p in paginas for i in p]


def _itens(paginas: Paginas) -> dict[Any, dict[str, Any]]:
    return {i.get("id_exames_lancados"): i for p in paginas for i in p}


def _leitura(paginas: Paginas) -> dict[str, Any]:
    ids = _ids(paginas)
    repetidos = [k for k, v in Counter(ids).items() if v > 1]
    return {
        "paginas": len(paginas),
        "itens_por_pagina": [len(p) for p in paginas],
        "itens": len(ids),
        "ids_unicos": len(set(ids)),
        "ids_repetidos_entre_paginas": sorted(repetidos, key=str),
    }


def _faltantes(referencia: Iterable[Any], leitura: Iterable[Any]) -> list[Any]:
    return sorted(set(referencia) - set(leitura), key=str)


def diagnosticar_exames(
    leitor: LeitorExames, dia: date, funcionario: int | None = None
) -> dict[str, Any]:
    base = {"dataExame_aPartirDe": dia.isoformat(), "dataExame_ate": dia.isoformat()}
    clinico = {**base, "exame": EXAME_CLINICO}

    sistema = leitor.exames_realizados_paginas(clinico)
    sistema_2 = leitor.exames_realizados_paginas(clinico)
    pagina_50 = leitor.exames_realizados_paginas(clinico, 50)
    pagina_25 = leitor.exames_realizados_paginas(clinico, 25)
    por_tipo: dict[str, Paginas] = {
        tipo: leitor.exames_realizados_paginas({**clinico, "tipo_exame": tipo})
        for tipo in TIPOS_EXAME
    }
    todos = leitor.exames_realizados_paginas(base)

    lidos = _itens(sistema)
    # Referência: união de tudo o que qualquer leitura devolveu para exame clínico no dia.
    referencia: dict[Any, dict[str, Any]] = {}
    for fonte in (sistema, sistema_2, pagina_50, pagina_25, *por_tipo.values()):
        referencia.update(_itens(fonte))

    # Nomes de exame do dia que parecem clínico mas não são "Clínico" exato.
    nomes = Counter(str(i.get("exame")) for p in todos for i in p)
    parecidos = {
        n: q for n, q in nomes.items() if "clinic" in _sem_acento(n) and n != EXAME_CLINICO
    }
    fora_do_filtro = [
        _resumo(i)
        for p in todos
        for i in p
        if str(i.get("exame")) in parecidos and i.get("id_exames_lancados") not in lidos
    ]

    ignorados: list[dict[str, Any]] = []
    for item in lidos.values():
        try:
            exame = para_exame_clinico(item)
        except ValueError as erro:
            ignorados.append({**_resumo(item), "motivo": str(erro)})
            continue
        if exame.data != dia:
            ignorados.append({**_resumo(item), "motivo": f"data {exame.data} diferente do dia"})

    perdidos = _faltantes(referencia, lidos)
    resultado: dict[str, Any] = {
        "dia": dia.isoformat(),
        "leitura_do_sistema": _leitura(sistema),
        "repeticao": {
            **_leitura(sistema_2),
            "ids_diferentes_da_primeira": sorted(
                set(_ids(sistema)) ^ set(_ids(sistema_2)), key=str
            ),
        },
        "paginas_de_50": {
            **_leitura(pagina_50),
            "ids_que_o_sistema_nao_leu": _faltantes(_ids(pagina_50), _ids(sistema)),
        },
        "paginas_de_25": {
            **_leitura(pagina_25),
            "ids_que_o_sistema_nao_leu": _faltantes(_ids(pagina_25), _ids(sistema)),
        },
        "por_tipo_de_exame": {
            tipo: {"itens": len(_ids(p)), "paginas": len(p)} for tipo, p in por_tipo.items()
        },
        "exames_que_o_sistema_nao_leu": [_resumo(referencia[i]) for i in perdidos],
        "nomes_de_exame_parecidos_com_clinico": parecidos,
        "clinicos_fora_do_filtro": fora_do_filtro,
        "itens_lidos_mas_descartados": ignorados,
    }
    if funcionario is not None:
        do_funcionario = leitor.exames_realizados_paginas({**base, "funcionario": str(funcionario)})
        resultado["funcionario"] = {
            "id": funcionario,
            "exames_no_dia": [
                {**_resumo(i), "lido_pelo_sistema": i.get("id_exames_lancados") in lidos}
                for p in do_funcionario
                for i in p
            ],
        }
    resultado["conclusao"] = _conclusao(resultado)
    return resultado


def _conclusao(r: Mapping[str, Any]) -> list[str]:
    linhas: list[str] = []
    perdidos = r["exames_que_o_sistema_nao_leu"]
    if (
        r["repeticao"]["ids_diferentes_da_primeira"]
        or r["leitura_do_sistema"]["ids_repetidos_entre_paginas"]
    ):
        linhas.append(
            "PAGINAÇÃO INSTÁVEL: a mesma consulta devolve listas diferentes ou repete itens "
            "entre páginas (a API não ordena de forma estável)."
        )
    if perdidos and not linhas:
        linhas.append(
            "Há exames clínicos do dia que a leitura do sistema não trouxe, sem sinal de "
            "paginação instável: compare o tamanho de página e o filtro de exame."
        )
    if r["clinicos_fora_do_filtro"]:
        linhas.append(
            "FILTRO: há exames com nome parecido com Clínico que o filtro exame=Clínico não pega."
        )
    if r["itens_lidos_mas_descartados"]:
        linhas.append("DESCARTE: itens lidos, mas recusados pelo sistema (veja o motivo).")
    if not linhas:
        linhas.append("Nada de errado na leitura do SGG neste dia; investigue a gravação no banco.")
    return linhas
