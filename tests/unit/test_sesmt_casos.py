"""Relatório do SESMT: coleta de todas as empresas, tolerância a falhas, envio com lista própria."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from relatorio.application.envio import RelatorioIndisponivel
from relatorio.application.modelos import StatusEnvio
from relatorio.application.sesmt import (
    CHAVE_NOMES_GRUPOS,
    FALHAS_SEGUIDAS_PARA_ABORTAR,
    GRUPOS_PADRAO,
    ColetaSesmtInviavel,
    formatar_nomes_grupos,
    ler_nomes_grupos,
)
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.sesmt import DocumentoSst, EmpresaSesmt, EventoEsocial
from tests.unit.conftest import Sistema

REF = date(2026, 9, 29)
NOITE = datetime(2026, 9, 29, 20, 0, tzinfo=FUSO_BRASILIA)
MANHA = datetime(2026, 9, 30, 8, 0, tzinfo=FUSO_BRASILIA)


def empresa(id_: int, esocial: bool = False, grupo: str = "1") -> EmpresaSesmt:
    return EmpresaSesmt(id_, f"Empresa {id_} LTDA", "", grupo, esocial)


@pytest.fixture
def sistema_sesmt(sistema: Sistema) -> Sistema:
    s = sistema.sesmt
    s.empresas = [empresa(1, esocial=True), empresa(2, esocial=True), empresa(3), empresa(4)]
    s.documentos = {
        1: [DocumentoSst(1, "PGR", date(2026, 10, 5))],  # vence no mês seguinte
        3: [DocumentoSst(3, "PCMSO", date(2026, 9, 1))],  # vencido
        4: [DocumentoSst(4, "PGR", date(2020, 1, 1))],  # vencido (todas as empresas entram)
    }
    s.eventos = {
        1: [EventoEsocial("1", "S-2220", 1, REF, "1.1.1")],
        2: [EventoEsocial("2", "S-2240", 2, REF, "")],
        3: [EventoEsocial("3", "S-2220", 3, REF, "1.1.3")],
    }
    sistema.relogio.instante = NOITE
    return sistema


def test_consulta_so_o_que_precisa_e_grava_o_resumo(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    detalhe = s.consolidar_sesmt.executar(REF)
    assert detalhe == {
        "referencia": "2026-09-29",
        "proximo_mes": 1,  # PGR da empresa 1
        "vencidos": 2,  # PCMSO da empresa 3 e PGR da 4
        "esocial_rejeitados": 1,  # a empresa 3 não tem o eSocial habilitado: não é consultada
        "empresas_sem_consulta": 0,
    }
    assert sorted(c for c in s.sesmt.chamadas if c.startswith("documentos")) == [
        f"documentos:{i}" for i in (1, 2, 3, 4)
    ]
    assert sorted(c for c in s.sesmt.chamadas if c.startswith("eventos")) == [
        "eventos:1",
        "eventos:2",
    ]
    m = s.banco.resumos_sesmt[REF].metricas
    assert (m["referencia"], m["hoje"]) == ("2026-09-29", "2026-09-30")
    assert (m["esocial"]["enviados"], m["esocial"]["rejeitados"]) == (2, 1)
    assert REF not in s.banco.resumos and REF not in s.banco.resumos_financeiros


def test_nomes_dos_grupos_padrao_e_configurados(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    s.consolidar_sesmt.executar(REF)
    item = s.banco.resumos_sesmt[REF].metricas["proximo_mes"]["empresas"][0]
    assert item["grupo"] == GRUPOS_PADRAO["1"]
    with s.uow() as uow:
        uow.configuracoes.definir(CHAVE_NOMES_GRUPOS, "1=Premium")
    s.consolidar_sesmt.executar(REF)
    assert s.banco.resumos_sesmt[REF].metricas["proximo_mes"]["empresas"][0]["grupo"] == "Premium"


def test_leitura_dos_nomes_dos_grupos() -> None:
    assert ler_nomes_grupos(None) == GRUPOS_PADRAO
    assert ler_nomes_grupos("") == {}  # salvo vazio no admin: mostra só o código
    nomes = ler_nomes_grupos(" 3 = Básico ;1=Premium\n=sem código; 4=")
    assert nomes == {"3": "Básico", "1": "Premium"}
    assert formatar_nomes_grupos(nomes) == "1=Premium; 3=Básico"


def test_empresa_que_falha_fica_de_fora_e_o_resumo_avisa(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    s.sesmt.empresas += [empresa(i, esocial=True) for i in range(20, 60)]  # 40 empresas a mais
    s.sesmt.falhar_empresas = {2}
    detalhe = s.consolidar_sesmt.executar(REF)
    assert detalhe["empresas_sem_consulta"] == 1
    assert detalhe["esocial_rejeitados"] == 0  # o rejeitado era da empresa 2, que não respondeu
    assert s.banco.resumos_sesmt[REF].metricas["empresas_sem_consulta"] == 1


def test_falha_geral_aborta_em_vez_de_mandar_relatorio_vazio(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    total = FALHAS_SEGUIDAS_PARA_ABORTAR + 2
    s.sesmt.empresas = [empresa(i, esocial=True) for i in range(1, total + 1)]
    s.sesmt.falhar_empresas = {e.id for e in s.sesmt.empresas}
    with pytest.raises(Exception, match="SGG indisponível"):
        s.consolidar_sesmt.executar(REF)
    assert REF not in s.banco.resumos_sesmt
    # Parou nas falhas seguidas, sem gastar a cota nas empresas restantes.
    assert len([c for c in s.sesmt.chamadas if c.startswith("documentos")]) == (
        FALHAS_SEGUIDAS_PARA_ABORTAR
    )
    assert not [c for c in s.sesmt.chamadas if c.startswith("eventos")]


def test_muitas_falhas_isoladas_tambem_inviabilizam(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    s.sesmt.empresas = [empresa(i, esocial=True) for i in range(1, 21)]
    s.sesmt.falhar_empresas = {2, 5, 8}  # 15%: acima do limite, mas nunca 5 seguidas
    with pytest.raises(ColetaSesmtInviavel, match="3 de 20"):
        s.consolidar_sesmt.executar(REF)


def test_envia_so_para_a_lista_do_sesmt(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    with s.uow() as uow:
        uow.destinatarios.adicionar("recepcao@multilife.com.br", None)
        uow.destinatarios_financeiro.adicionar("diretoria@multilife.com.br", None)
        uow.destinatarios_sesmt.adicionar("sesmt@multilife.com.br", None)
    s.relogio.instante = MANHA
    resultado = s.enviar_sesmt.executar_agendado(REF, tentativa=1)
    assert resultado["status"] == "enviado"  # consolidou na hora: não havia resumo
    ((destinatarios, conteudo),) = s.email.enviados
    assert destinatarios == ["sesmt@multilife.com.br"]
    assert conteudo.assunto == "SESMT 2026-09-29"
    assert s.banco.resumos_sesmt[REF].status_envio is StatusEnvio.ENVIADO
    assert s.enviar_sesmt.executar(REF)["status"] == "ja_enviado"
    assert s.verificar_sesmt.executar(REF)["status"] == "ok"


def test_sem_destinatarios_alerta_o_tecnico(sistema_sesmt: Sistema) -> None:
    s = sistema_sesmt
    s.consolidar_sesmt.executar(REF)
    assert s.enviar_sesmt.executar(REF)["status"] == "sem_destinatarios"
    assert s.alertas_enviados() == ["[Alerta] Relatório do SESMT sem destinatários"]


def test_sgg_fora_na_ultima_tentativa_alerta_e_nao_envia(sistema: Sistema) -> None:
    sistema.sesmt.falhar = True
    sistema.relogio.instante = NOITE
    with pytest.raises(Exception, match="SGG indisponível"):
        sistema.consolidar_sesmt.executar_agendado(REF, tentativa=1)
    assert sistema.alertas_enviados() == []
    with pytest.raises(Exception, match="SGG indisponível"):
        sistema.consolidar_sesmt.executar_agendado(REF, tentativa=2)
    assert sistema.alertas_enviados() == ["[Alerta] Relatório do SESMT não consolidado"]
    with pytest.raises(RelatorioIndisponivel):
        sistema.enviar_sesmt.executar(REF)
    assert sistema.email.enviados[-1][1].assunto == "[Alerta] Relatório do SESMT não enviado"


def test_coleta_da_noite_vence_pelo_dia_do_email(sistema_sesmt: Sistema) -> None:
    """Coletado às 20:00 de 29/09, o e-mail é de 30/09: o que vence em 30/09 não venceu."""
    s = sistema_sesmt
    s.sesmt.documentos = {1: [DocumentoSst(1, "PGR", date(2026, 9, 30))]}
    s.consolidar_sesmt.executar(REF)
    m = s.banco.resumos_sesmt[REF].metricas
    assert (m["hoje"], m["vencidos"]["total"]) == ("2026-09-30", 0)
    assert m["proximo_mes"]["inicio"] == "2026-10-01"
