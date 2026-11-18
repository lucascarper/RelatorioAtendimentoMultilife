"""Relatório do SESMT: vencidos por grupo, vencimentos do mês seguinte e eSocial do mês."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pytest

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import (
    DadosSesmt,
    DocumentoSst,
    EmpresaSesmt,
    EventoEsocial,
    calcular_sesmt,
    da_versao_atual,
    documentos_correntes,
    mes_seguinte,
    mesmo_periodo_mes_anterior,
)

REF = date(2026, 11, 17)  # coleta das 20:00 de 17/11
HOJE = date(2026, 11, 18)  # e-mail de 18/11
GERADO = datetime(2026, 11, 17, 23, 30, tzinfo=FUSO_BRASILIA)


def d(texto: str) -> date:
    return date.fromisoformat(texto)


def empresa(id_: int, nome: str, grupo: str = "2", cnpj: str = "") -> EmpresaSesmt:
    return EmpresaSesmt(id_, nome, cnpj, grupo, True)


EMPRESAS = {
    1: empresa(1, "Alfa LTDA", "1", "11.111.111/0001-11"),
    2: empresa(2, "Beta LTDA", "4"),
    3: empresa(3, "Gama LTDA", ""),
    4: empresa(4, "Delta LTDA", "14"),
}


def dados(**campos: Any) -> DadosSesmt:
    base: dict[str, Any] = {
        "referencia": REF,
        "hoje": HOJE,
        "empresas": EMPRESAS,
        "documentos": [],
        "eventos": [],
        "nomes_grupos": {"1": "GESTÃO PREMIUM", "4": "EVENTUAL"},
    }
    base.update(campos)
    return DadosSesmt(**base)


def doc(id_empresa: int, tipo: str, vence: str, emissao: str | None = None) -> DocumentoSst:
    return DocumentoSst(id_empresa, tipo, d(vence), d(emissao) if emissao else None)


def evento(codigo: int, dia: str, empresa_: int = 1, recibo: str = "1.1.1") -> EventoEsocial:
    return EventoEsocial(str(codigo), "S-2220", empresa_, d(dia), recibo)


def test_datas_do_mes_seguinte_e_do_mes_anterior() -> None:
    assert mes_seguinte(d("2026-11-18")) == (d("2026-12-01"), d("2026-12-31"))
    assert mes_seguinte(d("2026-12-31")) == (d("2027-01-01"), d("2027-01-31"))
    assert mesmo_periodo_mes_anterior(d("2026-11-17")) == (d("2026-10-01"), d("2026-10-17"))
    assert mesmo_periodo_mes_anterior(d("2026-03-31")) == (d("2026-02-01"), d("2026-02-28"))
    assert mesmo_periodo_mes_anterior(d("2027-01-05")) == (d("2026-12-01"), d("2026-12-05"))


def test_documento_corrente_e_o_mais_recente() -> None:
    antigo = doc(1, "PGR", "2025-01-01", "2024-01-01")
    novo = doc(1, "PGR", "2027-01-01", "2026-01-01")
    sem_emissao = DocumentoSst(1, "PCMSO", d("2026-01-01"))
    renovado = DocumentoSst(1, "PCMSO", d("2027-02-01"))
    assert set(documentos_correntes([antigo, novo, sem_emissao, renovado])) == {novo, renovado}


def test_vencidos_por_grupo_divididos_por_tipo() -> None:
    m = calcular_sesmt(
        dados(
            documentos=[
                doc(1, "PGR", "2026-11-17"),  # venceu ontem
                doc(1, "PCMSO", "2026-11-18"),  # vence hoje: ainda não venceu
                doc(2, "PGR", "2025-01-01"),
                doc(2, "LTCAT", "2026-06-01"),
                doc(3, "PCMSO", "2026-10-01"),
                doc(4, "LTCAT", "2026-10-01"),
                doc(4, "LTCAT", "2027-10-01", "2026-10-02"),  # renovado: não conta
            ]
        ),
        GERADO,
    )
    v = m["vencidos"]
    assert (v["total"], v["empresas"]) == (4, 3)
    assert v["por_tipo"] == {"PGR": 2, "PCMSO": 1, "LTCAT": 1}
    assert v["por_grupo"] == [
        {"grupo": "EVENTUAL", "total": 2, "por_tipo": {"PGR": 1, "PCMSO": 0, "LTCAT": 1}},
        {"grupo": "GESTÃO PREMIUM", "total": 1, "por_tipo": {"PGR": 1, "PCMSO": 0, "LTCAT": 0}},
        {"grupo": "Sem grupo", "total": 1, "por_tipo": {"PGR": 0, "PCMSO": 1, "LTCAT": 0}},
    ]
    # A lista completa (planilha) vai do mais atrasado ao menos.
    assert [(x["empresa"], x["tipo"]) for x in v["itens"]][:2] == [
        ("Beta LTDA", "PGR"),
        ("Beta LTDA", "LTCAT"),
    ]


def test_grupo_sem_nome_aparece_pelo_codigo() -> None:
    m = calcular_sesmt(dados(documentos=[doc(4, "PGR", "2026-01-01")]), GERADO)
    assert m["vencidos"]["por_grupo"][0]["grupo"] == "Grupo 14"


def test_vencem_no_mes_civil_seguinte_agrupados_por_empresa() -> None:
    m = calcular_sesmt(
        dados(
            documentos=[
                doc(1, "PCMSO", "2026-12-20"),
                doc(1, "PGR", "2026-12-05"),
                doc(2, "PGR", "2026-12-01"),
                doc(3, "PGR", "2026-11-30"),  # ainda neste mês: fora
                doc(3, "LTCAT", "2027-01-01"),  # mês depois do seguinte: fora
            ]
        ),
        GERADO,
    )
    p = m["proximo_mes"]
    assert (p["inicio"], p["fim"]) == ("2026-12-01", "2026-12-31")
    assert (p["total"], p["empresas_total"]) == (3, 2)
    assert [e["empresa"] for e in p["empresas"]] == ["Beta LTDA", "Alfa LTDA"]
    assert p["empresas"][1]["documentos"] == [
        {"tipo": "PGR", "vencimento": "2026-12-05"},
        {"tipo": "PCMSO", "vencimento": "2026-12-20"},
    ]
    assert p["empresas"][1]["cnpj"] == "11.111.111/0001-11"


def test_esocial_do_mes_ate_a_referencia_com_rejeitados_por_empresa() -> None:
    eventos = [
        evento(1, "2026-11-01"),
        evento(2, "2026-11-17", recibo=""),
        evento(3, "2026-11-10", empresa_=2, recibo=""),
        EventoEsocial("4", "S-2240", 2, d("2026-11-11"), ""),
        evento(5, "2026-11-18", recibo=""),  # depois da referência
        evento(6, "2026-10-31", recibo=""),  # mês anterior
        evento(7, "2026-10-17"),
        evento(8, "2026-10-18"),  # depois do mesmo dia do mês anterior
    ]
    e = calcular_sesmt(dados(eventos=eventos), GERADO)["esocial"]
    assert (e["inicio"], e["fim"], e["enviados"], e["rejeitados"]) == (
        "2026-11-01",
        "2026-11-17",
        4,
        3,
    )
    assert e["anterior"] == {
        "inicio": "2026-10-01",
        "fim": "2026-10-17",
        "enviados": 1,
        "rejeitados": 0,
    }
    assert e["empresas_rejeitadas"] == 2
    assert [(x["empresa"], x["quantidade"], x["tipos"]) for x in e["rejeitados_por_empresa"]] == [
        ("Beta LTDA", 2, {"S-2220": 1, "S-2240": 1}),
        ("Alfa LTDA", 1, {"S-2220": 1}),
    ]


def test_esocial_mes_a_mes_nos_ultimos_12_meses() -> None:
    eventos = [evento(1, "2026-11-02", recibo=""), evento(2, "2026-10-31"), evento(3, "2025-11-30")]
    mensal = calcular_sesmt(dados(eventos=eventos), GERADO)["esocial_mensal"]
    assert len(mensal) == 12
    assert mensal[0] == {"mes": "2025-12", "enviados": 0, "rejeitados": 0}  # nov/25 fica fora
    assert mensal[-2:] == [
        {"mes": "2026-10", "enviados": 1, "rejeitados": 0},
        {"mes": "2026-11", "enviados": 1, "rejeitados": 1},
    ]


def test_resumo_e_json_puro_sem_dados_do_trabalhador() -> None:
    m = calcular_sesmt(
        dados(
            documentos=[doc(1, "PGR", "2026-01-01")], eventos=[evento(1, "2026-11-03", recibo="")]
        ),
        GERADO,
    )
    texto = json.dumps(m, ensure_ascii=False)
    assert "funcionario" not in texto.lower()
    assert m["versao_regra"].startswith("2.")
    assert (m["referencia"], m["hoje"], m["empresas_consultadas"]) == (
        "2026-11-17",
        "2026-11-18",
        4,
    )


@pytest.mark.parametrize(
    ("versao", "atual"), [("2.0.0", True), ("2.3.1", True), ("1.1.0", False), ("", False)]
)
def test_resumos_da_versao_antiga_tem_outro_formato(versao: str, atual: bool) -> None:
    assert da_versao_atual({"versao_regra": versao}) is atual
