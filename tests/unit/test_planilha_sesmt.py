"""Leitura das planilhas de vencimento do SGG (PGR, PCMSO, LTCAT) enviadas no admin."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from relatorio.infrastructure.planilha_sesmt import PlanilhaInvalida, ler_planilha_vencimentos
from tests.fabricas import planilha_vencimentos


def test_le_codigo_empresa_grupo_e_validade_e_descarta_contatos() -> None:
    conteudo = planilha_vencimentos(
        "PCMSO",
        [
            (60, "A.M COMERCIO LTDA", " 02.688.133/0001-28", "GESTÃO 2", "28/01/2028", "a@b.com"),
            ("1818", "PEIXE NA REDE", "", "EVENTUAL", datetime(2026, 11, 8), "c@d.com"),
            (77, "Maria da Silva", "123.456.789-09", "", "01/12/2026", "maria@e.com"),
            (None, "linha sem código", "", "", "01/01/2027", ""),
            (78, "validade ilegível", "", "", "sem data", ""),
            (None, None, None, None, None, None),  # linha vazia no fim: não conta
        ],
    )
    p = ler_planilha_vencimentos(conteudo)
    assert (p.tipo, p.ignoradas) == ("PCMSO", 2)
    assert [
        (d.id_empresa, d.tipo, d.vencimento, d.empresa, d.cnpj, d.grupo) for d in p.documentos
    ] == [
        (60, "PCMSO", date(2028, 1, 28), "A.M COMERCIO LTDA", "02.688.133/0001-28", "GESTÃO 2"),
        (1818, "PCMSO", date(2026, 11, 8), "PEIXE NA REDE", "", "EVENTUAL"),
        (77, "PCMSO", date(2026, 12, 1), "Empresa #77", "", ""),  # CPF: nome não fica (LGPD)
    ]
    assert "@" not in repr(p)


def test_arquivo_que_nao_e_planilha_ou_sem_colunas_e_recusado() -> None:
    with pytest.raises(PlanilhaInvalida, match="xlsx"):
        ler_planilha_vencimentos(b"nome;validade\n1;01/01/2027")
    with pytest.raises(PlanilhaInvalida, match="PGR, PCMSO ou LTCAT"):
        ler_planilha_vencimentos(planilha_vencimentos("ASO", [(1, "X", "", "", "01/01/2027", "")]))
