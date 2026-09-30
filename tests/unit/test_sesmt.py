"""Relatório do SESMT: pendências (a vencer, vencidos) e eventos do eSocial."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import (
    MAX_LINHAS_PENDENCIAS,
    ContratoSesmt,
    DadosSesmt,
    DocumentoSst,
    EmpresaSesmt,
    EventoEsocial,
    calcular_sesmt,
    documentos_correntes,
)

REF = date(2026, 9, 29)
HOJE = date(2026, 9, 30)
GERADO = datetime(2026, 9, 30, 2, 0, tzinfo=FUSO_BRASILIA)


def d(texto: str) -> date:
    return date.fromisoformat(texto)


def empresa(
    id_: int, nome: str, grupo: str = "2", cnpj: str = "11.111.111/0001-11"
) -> EmpresaSesmt:
    return EmpresaSesmt(id_, nome, cnpj, grupo, True)


EMPRESAS = {
    1: empresa(1, "Alfa LTDA", "1"),
    2: empresa(2, "Beta LTDA", "4"),
    3: empresa(3, "Gama LTDA", ""),
}


def dados(**campos: Any) -> DadosSesmt:
    base: dict[str, Any] = {
        "referencia": REF,
        "hoje": HOJE,
        "empresas": EMPRESAS,
        "contratos": [],
        "documentos": [],
        "eventos": [],
        "nomes_grupos": {"1": "GESTÃO PREMIUM"},
    }
    base.update(campos)
    return DadosSesmt(**base)


def evento(
    codigo: int, hora: str | None, recibo: str = "1.1.001", dia: date = REF
) -> EventoEsocial:
    gerado = (
        datetime.fromisoformat(f"{dia.isoformat()}T{hora}").replace(tzinfo=FUSO_BRASILIA)
        if hora
        else None
    )
    return EventoEsocial(str(codigo), "S-2220", 1, 100 + codigo, dia, gerado, None, recibo, "")


def test_documento_corrente_e_o_de_maior_vencimento() -> None:
    docs = [
        DocumentoSst(1, "PGR", d("2024-11-22")),
        DocumentoSst(1, "PGR", d("2026-08-08")),
        DocumentoSst(1, "PCMSO", d("2026-10-20")),
        DocumentoSst(1, "LTCAT", None),  # sem vencimento: ignorado
    ]
    correntes = {(c.tipo, c.vencimento) for c in documentos_correntes(docs)}
    assert correntes == {("PGR", d("2026-08-08")), ("PCMSO", d("2026-10-20"))}


def test_renovado_nao_gera_vencido_e_o_atrasado_mostra_dias() -> None:
    r = calcular_sesmt(
        dados(
            documentos=[
                DocumentoSst(1, "PGR", d("2024-01-01")),  # substituído pelo de baixo
                DocumentoSst(1, "PGR", d("2027-01-01")),
                DocumentoSst(2, "LTCAT", d("2026-09-10")),
            ]
        ),
        GERADO,
    )
    assert r["vencidos"]["total"] == 1
    (item,) = r["vencidos"]["itens"]
    assert (item["identificacao"], item["dias"], item["status"]) == (
        "LTCAT - Beta LTDA",
        -20,
        "Vencido",
    )
    assert item["grupo"] == "Grupo 4"  # sem nome configurado: mostra o código
    assert r["a_vencer"]["total"] == 0


def test_a_vencer_janela_status_e_ordem() -> None:
    r = calcular_sesmt(
        dados(
            documentos=[
                DocumentoSst(1, "PCMSO", d("2026-10-15")),  # 15 dias
                DocumentoSst(2, "PGR", d("2026-10-07")),  # 7 dias: vencendo
                DocumentoSst(3, "LTCAT", d("2026-10-30")),  # 30 dias: ainda entra
                DocumentoSst(1, "PGR", d("2026-10-31")),  # 31 dias: fora
                DocumentoSst(2, "PCMSO", HOJE),  # vence hoje: entra como vencendo
            ]
        ),
        GERADO,
    )
    bloco = r["a_vencer"]
    assert [(i["tipo"], i["dias"], i["status"]) for i in bloco["itens"]] == [
        ("PCMSO", 0, "Vencendo"),
        ("PGR", 7, "Vencendo"),
        ("PCMSO", 15, "A vencer"),
        ("LTCAT", 30, "A vencer"),
    ]
    assert (bloco["total"], bloco["documentos"], bloco["contratos"], bloco["vencendo"]) == (
        4,
        4,
        0,
        2,
    )
    assert bloco["itens"][2]["grupo"] == "GESTÃO PREMIUM"


def test_so_o_contrato_corrente_conta() -> None:
    r = calcular_sesmt(
        dados(
            contratos=[
                ContratoSesmt(1, 1, d("2026-10-10"), "Em andamento", True, "2025-089"),
                ContratoSesmt(2, 1, d("2023-01-01"), "Vencido", False),  # substituído
                ContratoSesmt(3, 2, d("2026-09-01"), "Vencido", True),
                ContratoSesmt(4, 3, d("2025-01-01"), "Vencido", True),  # há mais de 90 dias
                ContratoSesmt(5, 3, None, "Em andamento", True),  # sem data: ignorado
            ]
        ),
        GERADO,
    )
    a_vencer, vencidos = r["a_vencer"], r["vencidos"]
    assert [(i["identificacao"], i["categoria"]) for i in a_vencer["itens"]] == [
        ("CT-2025-089 - Alfa LTDA", "contrato")
    ]
    assert [i["identificacao"] for i in vencidos["itens"]] == ["CT-3 - Beta LTDA"]
    assert (vencidos["total"], vencidos["contratos_antigos"]) == (1, 1)


def test_documento_vencido_ha_muito_tempo_continua_na_lista() -> None:
    """Cliente com contrato em andamento e programa vencido é risco legal: sem janela."""
    r = calcular_sesmt(dados(documentos=[DocumentoSst(1, "PGR", d("2024-12-14"))]), GERADO)
    assert [(i["dias"], i["status"]) for i in r["vencidos"]["itens"]] == [(-655, "Vencido")]


def test_empresa_sem_cadastro_e_sem_grupo() -> None:
    r = calcular_sesmt(dados(documentos=[DocumentoSst(99, "PGR", d("2026-10-01"))]), GERADO)
    (item,) = r["a_vencer"]["itens"]
    assert (item["empresa"], item["grupo"]) == ("Empresa #99", "Sem grupo")
    r = calcular_sesmt(dados(documentos=[DocumentoSst(3, "PGR", d("2026-10-01"))]), GERADO)
    assert r["a_vencer"]["itens"][0]["grupo"] == "Sem grupo"  # grupo vazio


def test_lista_de_pendencias_e_limitada_mas_o_total_nao() -> None:
    docs = [DocumentoSst(i, "PGR", d("2020-01-01")) for i in range(100, 100 + 55)]
    r = calcular_sesmt(dados(documentos=docs), GERADO)
    bloco = r["vencidos"]
    assert bloco["total"] == 55
    assert len(bloco["itens"]) == MAX_LINHAS_PENDENCIAS
    assert bloco["ocultos"] == 55 - MAX_LINHAS_PENDENCIAS


def test_esocial_so_da_data_de_referencia_com_recibo_e_sem_recibo() -> None:
    r = calcular_sesmt(
        dados(
            eventos=[
                evento(1, "16:45:50"),
                evento(2, "09:15:22"),
                evento(3, "11:00:00", recibo=""),
                evento(4, "10:00:00", dia=d("2026-09-28")),  # outro dia
                evento(5, None),  # sem hora: vai para o fim
            ]
        ),
        GERADO,
    )
    e = r["esocial"]
    assert (e["total"], e["com_recibo"], e["sem_recibo"]) == (4, 3, 1)
    assert e["por_evento"] == {"S-2220": 4}
    assert [x["horario"] for x in e["eventos"]] == [None, "09:15:22", "16:45:50"]
    assert [x["codigo"] for x in e["pendentes"]] == ["3"]
    assert e["pendentes"][0]["recibo"] is None


def test_esocial_nao_expoe_dado_pessoal() -> None:
    r = calcular_sesmt(dados(eventos=[evento(7, "10:00:00")]), GERADO)
    (linha,) = r["esocial"]["eventos"]
    assert linha["funcionario"] == 107  # só o código do SGG
    assert set(linha) == {
        "codigo",
        "horario",
        "evento",
        "funcionario",
        "empresa",
        "cnpj",
        "recibo",
        "protocolo",
    }


def test_dia_sem_eventos_e_resumo_serializavel() -> None:
    r = calcular_sesmt(dados(empresas_sem_consulta=2), GERADO)
    json.dumps(r)  # vai para JSONB: não pode ter tipos fora do JSON
    assert r["esocial"]["total"] == 0
    assert r["empresas_sem_consulta"] == 2
    assert (r["referencia"], r["hoje"], r["versao_regra"]) == ("2026-09-29", "2026-09-30", "1.0.0")
