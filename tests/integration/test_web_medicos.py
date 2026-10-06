"""Configurações → Médicos (atendimentos por médico) contra o PostgreSQL."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from relatorio.application.ports import FabricaUoW
from relatorio.domain.exames import ExameClinico, Medico
from relatorio.domain.sesmt import EmpresaSesmt
from relatorio.infrastructure.container import Container
from tests.fabricas import DIA
from tests.fakes import ExamesFake
from tests.integration.test_web import (  # noqa: F401
    cliente,
    container,
    csrf,
    entrar,
    relogio,
)
from tests.integration.test_web_usuarios import criar, entrar_como, outro  # noqa: F401

pytestmark = pytest.mark.integration

URL = "/admin/configuracoes/medicos"
ANA = Medico("34985-DF", "Ana Souza")
BETO = Medico("31096-DF", "Beto Lima")


class SggDeTeste(ExamesFake):
    def fechar(self) -> None:
        pass


@pytest.fixture
def sgg(container: Container) -> SggDeTeste:  # noqa: F811
    fake = SggDeTeste(
        exames={
            DIA: [
                ExameClinico(1, DIA, 10, 501, ANA.crm, ANA.nome, "Admissional", funcionario="F1"),
                ExameClinico(2, DIA, 10, 502, BETO.crm, BETO.nome, "Periódico", funcionario="F2"),
            ]
        },
        empresas={10: EmpresaSesmt(10, "Conplan LTDA", "", "1", False)},
        medicos={"123-RS": Medico("123-RS", "Caio Reis")},
    )
    container.__dict__["sgg"] = fake  # antes de montar os casos de uso
    return fake


def test_aba_medicos_vazia_explica_o_primeiro_passo(
    cliente: TestClient,  # noqa: F811
    sgg: SggDeTeste,
) -> None:
    entrar(cliente)
    pagina = cliente.get(URL)
    assert pagina.status_code == 200
    assert 'href="/admin/configuracoes/medicos" aria-current="page">Médicos' in pagina.text
    assert "A lista aparece depois do primeiro processamento" in pagina.text
    assert "0 no relatório" in pagina.text


def test_processar_lista_medicos_e_escolher_um(
    cliente: TestClient,  # noqa: F811
    sgg: SggDeTeste,
    uow: FabricaUoW,
) -> None:
    token = entrar(cliente)
    resposta = cliente.post(f"{URL}/processar", data={"data": DIA.isoformat(), "csrf": token})
    assert resposta.status_code == 200  # redireciona para a lista (processada em segundo plano)
    assert "Processamento dos exames de" in resposta.text
    assert "Ana Souza" in resposta.text and "Beto Lima" in resposta.text
    assert "Clínicos no SGG" in resposta.text

    linha = cliente.post(
        f"{URL}/{ANA.crm}/selecionado",
        data={"selecionado": "true"},
        headers={"X-CSRF-Token": token},
    )
    assert linha.status_code == 200
    assert 'aria-checked="true"' in linha.text and "No relatório" in linha.text
    with uow() as u:
        assert [m.crm for m in u.medicos.selecionados()] == [ANA.crm]
        assert u.exames.do_dia(DIA) == []  # processado antes da escolha: nada gravado ainda


def test_cadastrar_crm(cliente: TestClient, sgg: SggDeTeste, uow: FabricaUoW) -> None:  # noqa: F811
    token = entrar(cliente)
    invalido = cliente.post(URL, data={"crm": "abc", "csrf": token})
    assert "Informe o CRM no formato" in invalido.text
    ausente = cliente.post(URL, data={"crm": "999-SP", "csrf": token})
    assert "O CRM 999-SP não está no cadastro" in ausente.text
    ok = cliente.post(URL, data={"crm": "123rs", "csrf": token})
    assert "Caio Reis (CRM 123-RS) entra nos atendimentos por médico." in ok.text
    assert "Cadastrado manualmente" in ok.text
    with uow() as u:
        assert [m.crm for m in u.medicos.selecionados()] == ["123-RS"]


def test_data_futura_nao_processa(
    cliente: TestClient,  # noqa: F811
    sgg: SggDeTeste,
    container: Container,  # noqa: F811
) -> None:
    token = entrar(cliente)
    amanha = container.relogio.agora().date() + timedelta(days=1)
    resposta = cliente.post(f"{URL}/processar", data={"data": amanha.isoformat(), "csrf": token})
    assert "Não é possível processar uma data futura." in resposta.text
    assert sgg.chamadas == []


def test_sem_configuracoes_nao_entra(
    cliente: TestClient,  # noqa: F811
    outro: TestClient,  # noqa: F811
    sgg: SggDeTeste,
) -> None:
    token = entrar(cliente)
    criar(cliente, token, modulos=("atendimento",))
    assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
    assert outro.get(URL).status_code == 403


def test_destinatario_autorizado_a_receber_a_planilha(
    cliente: TestClient,  # noqa: F811
    uow: FabricaUoW,
) -> None:
    token = entrar(cliente)
    cliente.post("/admin/destinatarios", data={"email": "rh@multilife.com.br", "csrf": token})
    pagina = cliente.get("/admin/atendimento")
    assert "Planilha (LGPD)" in pagina.text and "Não recebe" in pagina.text
    with uow() as u:
        (destinatario,) = [d for d in u.destinatarios.listar() if d.email == "rh@multilife.com.br"]
    assert not destinatario.recebe_anexo  # ninguém recebe por padrão
    linha = cliente.post(
        f"/admin/destinatarios/{destinatario.id}/anexo",
        data={"recebe": "true"},
        headers={"X-CSRF-Token": token},
    )
    assert linha.status_code == 200 and "</span>Recebe\n" in linha.text
    with uow() as u:
        assert next(d for d in u.destinatarios.listar() if d.id == destinatario.id).recebe_anexo
    # Desativar revoga a planilha (reativar exige autorizar de novo).
    cliente.post(
        f"/admin/destinatarios/{destinatario.id}/ativo",
        data={"ativo": "false"},
        headers={"X-CSRF-Token": token},
    )
    with uow() as u:
        assert not next(d for d in u.destinatarios.listar() if d.id == destinatario.id).recebe_anexo
    # As listas do financeiro e do SESMT não têm a coluna nem a opção.
    assert "Planilha (LGPD)" not in cliente.get("/admin/sesmt").text
    with uow() as u, pytest.raises(ValueError, match="atendimentos"):
        u.destinatarios_sesmt.definir_anexo(1, True)


def test_so_quem_tem_configuracoes_autoriza_a_planilha(
    cliente: TestClient,  # noqa: F811
    outro: TestClient,  # noqa: F811
) -> None:
    token = entrar(cliente)
    cliente.post("/admin/destinatarios", data={"email": "rh@multilife.com.br", "csrf": token})
    criar(cliente, token, modulos=("atendimento",))
    assert entrar_como(outro, "ana", "senha-da-ana-1") == 303
    pagina = outro.get("/admin/atendimento")
    assert "Planilha (LGPD)" in pagina.text
    assert "/anexo" not in pagina.text  # vê a situação, mas sem a chave
    assert "Só quem tem o módulo Configurações altera" in pagina.text
    token_ana = csrf(outro, "/admin/atendimento")
    resposta = outro.post(
        "/admin/destinatarios/1/anexo",
        data={"recebe": "true"},
        headers={"X-CSRF-Token": token_ana},
    )
    assert resposta.status_code == 403
