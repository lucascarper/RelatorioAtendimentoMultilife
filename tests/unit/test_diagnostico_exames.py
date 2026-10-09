"""Diagnóstico dos exames por médico: identifica paginação instável e filtro que perde exames."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from relatorio.interfaces.diagnostico_exames import diagnosticar_exames

DIA = date(2026, 8, 25)


def item(n: int, exame: str = "Clínico", tipo: str = "Periódico") -> dict[str, Any]:
    return {
        "id_exames_lancados": n,
        "data_exames_lancados": DIA.isoformat(),
        "exame": exame,
        "tipo_exame": tipo,
        "medico": "34630-DF",
        "nome_medico": "Fulano",
        "nome_funcionario": "Nome Que Nao Pode Sair",
        "id_funcionario": 1000 + n,
        "id_empresa": 7,
    }


class Leitor:
    """Base de 123 exames; ``instavel`` perde o item 50 quando o tamanho de página é 100."""

    def __init__(self, instavel: bool = False, extras: list[dict[str, Any]] | None = None) -> None:
        self.base = [item(n) for n in range(1, 124)]
        self.extras = extras or []
        self.instavel = instavel

    def exames_realizados_paginas(
        self, filtros: Mapping[str, str], tamanho: int = 100
    ) -> list[list[dict[str, Any]]]:
        itens = self.base + self.extras
        if filtros.get("exame") == "Clínico":
            itens = [i for i in itens if i["exame"] == "Clínico"]
        if "tipo_exame" in filtros:
            itens = [i for i in itens if i["tipo_exame"] == filtros["tipo_exame"]]
        if "funcionario" in filtros:
            itens = [i for i in itens if str(i["id_funcionario"]) == filtros["funcionario"]]
        if self.instavel and tamanho == 100 and filtros.get("exame") == "Clínico":
            itens = [i for i in itens if i["id_exames_lancados"] != 50]
        paginas = [itens[i : i + tamanho] for i in range(0, len(itens), tamanho)]
        return paginas or [[]]


def test_leitura_sem_problema() -> None:
    r = diagnosticar_exames(Leitor(), DIA)
    assert r["leitura_do_sistema"]["itens"] == 123
    assert r["exames_que_o_sistema_nao_leu"] == []
    assert "investigue a gravação" in r["conclusao"][0]


def test_exame_que_o_sistema_nao_leu_e_apontado_sem_dados_pessoais() -> None:
    r = diagnosticar_exames(Leitor(instavel=True), DIA, funcionario=1050)
    (perdido,) = r["exames_que_o_sistema_nao_leu"]
    assert perdido["id"] == 50 and perdido["id_funcionario"] == 1050
    assert r["paginas_de_50"]["ids_que_o_sistema_nao_leu"] == [50]
    assert r["funcionario"]["exames_no_dia"][0]["lido_pelo_sistema"] is False
    assert "Nome Que Nao Pode Sair" not in str(r) and "Fulano" not in str(r)


def test_nome_de_exame_parecido_com_clinico_fora_do_filtro() -> None:
    r = diagnosticar_exames(Leitor(extras=[item(500, exame="Exame Clínico Ocupacional")]), DIA)
    assert r["nomes_de_exame_parecidos_com_clinico"] == {"Exame Clínico Ocupacional": 1}
    assert [i["id"] for i in r["clinicos_fora_do_filtro"]] == [500]
    assert any("FILTRO" in c for c in r["conclusao"])
