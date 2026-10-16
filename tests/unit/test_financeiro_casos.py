"""Relatório financeiro: consolidação, envio com lista própria e retentativas."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from relatorio.application.envio import RelatorioIndisponivel
from relatorio.application.financeiro import (
    CHAVE_NOMES_CENTROS,
    FluxoDoDia,
    formatar_nomes_centros,
    ler_nomes_centros,
)
from relatorio.application.modelos import StatusEnvio
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.domain.financeiro import Contrato, ItemFaturado, PrecoFornecedor, Titulo
from tests.unit.conftest import Sistema

REF = date(2026, 9, 27)
MANHA = datetime(2026, 9, 28, 5, 45, tzinfo=FUSO_BRASILIA)


def titulo(id_: int, valor: str, **campos: object) -> Titulo:
    base: dict[str, object] = {
        "situacao": "Paga",
        "cancelada": False,
        "emissao": None,
        "vencimento": None,
        "pagamento": None,
    }
    base.update(campos)
    return Titulo(id=id_, valor=Decimal(valor), **base)  # type: ignore[arg-type]


@pytest.fixture
def sistema_financeiro(sistema: Sistema) -> Sistema:
    f = sistema.financeiro
    f.recebidos = [titulo(1, "500.00", pagamento=REF, rateio=(("2", Decimal("500")),))]
    f.pagos = [titulo(2, "120.00", pagamento=REF, classificacao="ADMINISTRATIVAS")]
    f.emitidos = [
        titulo(
            3,
            "300.00",
            emissao=REF,
            id_cliente=10,
            nome="Empresa Dez",
            itens=(ItemFaturado(11, "Audiometria Tonal (0281)", 6, Decimal("300.00")),),
        )
    ]
    f.contratos = [Contrato(1, 10, REF + timedelta(days=10), "Em andamento")]
    f.precos = {11: [PrecoFornecedor(11, 2, None, Decimal("50"), Decimal("20"))]}
    sistema.relogio.instante = MANHA
    return sistema


def test_consolida_e_grava_o_resumo_do_dia_anterior(sistema_financeiro: Sistema) -> None:
    s = sistema_financeiro
    with s.uow() as uow:
        uow.configuracoes.definir(CHAVE_NOMES_CENTROS, "2=Clínica")
    detalhe = s.consolidar_financeiro.executar(REF)
    assert detalhe == {
        "referencia": "2026-09-27",
        "saldo_dia": 380.0,
        "faturamento_mes": 300.0,
        "contratos_ativos": 1,
    }
    registro = s.banco.resumos_financeiros[REF]
    m = registro.metricas
    assert (m["referencia"], m["hoje"]) == ("2026-09-27", "2026-09-28")
    assert m["rateio"]["centros"][0]["centro"] == "Clínica"
    assert m["margem"][0]["custo"] == 120.0  # tabela de preços lida só para o exame faturado
    assert "precos_11" in s.financeiro.chamadas
    assert REF not in s.banco.resumos  # não mexe no resumo do relatório de atendimentos


def test_envia_so_para_a_lista_do_financeiro(sistema_financeiro: Sistema) -> None:
    s = sistema_financeiro
    with s.uow() as uow:
        uow.destinatarios.adicionar("recepcao@multilife.com.br", None)
        uow.destinatarios_financeiro.adicionar("diretoria@multilife.com.br", None)
    s.relogio.instante = MANHA.replace(hour=7, minute=59)
    resultado = s.enviar_financeiro.executar_agendado(REF, tentativa=1)
    assert resultado["status"] == "enviado"  # consolidou na hora: não havia resumo
    ((destinatarios, conteudo),) = s.email.enviados
    assert destinatarios == ["diretoria@multilife.com.br"]
    assert conteudo.assunto == "Financeiro 2026-09-27"
    assert conteudo.texto == "fin fluxo=2"  # a receita e a despesa pagas no dia, lidas do SGG
    assert s.banco.resumos_financeiros[REF].status_envio is StatusEnvio.ENVIADO
    assert s.enviar_financeiro.executar(REF)["status"] == "ja_enviado"
    assert s.verificar_financeiro.executar(REF)["status"] == "ok"


def test_sem_destinatarios_alerta_o_tecnico(sistema_financeiro: Sistema) -> None:
    s = sistema_financeiro
    s.consolidar_financeiro.executar(REF)
    assert s.enviar_financeiro.executar(REF)["status"] == "sem_destinatarios"
    assert s.alertas_enviados() == ["[Alerta] Relatório financeiro sem destinatários"]


def test_sgg_fora_na_ultima_tentativa_alerta_e_nao_envia(sistema: Sistema) -> None:
    sistema.financeiro.falhar = True
    sistema.relogio.instante = MANHA
    with pytest.raises(Exception, match="SGG indisponível"):
        sistema.consolidar_financeiro.executar_agendado(REF, tentativa=1)
    assert sistema.alertas_enviados() == []
    with pytest.raises(Exception, match="SGG indisponível"):
        sistema.consolidar_financeiro.executar_agendado(REF, tentativa=3)
    assert sistema.alertas_enviados() == ["[Alerta] Relatório financeiro não consolidado"]
    with pytest.raises(RelatorioIndisponivel):
        sistema.enviar_financeiro.executar(REF)
    assert sistema.email.enviados[-1][1].assunto == "[Alerta] Relatório financeiro não enviado"


def test_sem_alteracao_compara_com_o_resumo_de_ontem(sistema_financeiro: Sistema) -> None:
    s = sistema_financeiro
    s.consolidar_financeiro.executar(REF - timedelta(days=1))
    s.consolidar_financeiro.executar(REF)
    assert "contratos_ativos" in s.banco.resumos_financeiros[REF].metricas["sem_alteracao"]


def test_nomes_dos_centros_de_custo() -> None:
    nomes = ler_nomes_centros(" 3 = Ocupacional ;2=Clínica\n=sem código; 4=")
    assert nomes == {"3": "Ocupacional", "2": "Clínica"}
    assert formatar_nomes_centros(nomes) == "2=Clínica; 3=Ocupacional"


def test_fluxo_do_dia_lista_receitas_e_despesas_sem_gravar_a_descricao(
    sistema_financeiro: Sistema,
) -> None:
    s = sistema_financeiro
    s.financeiro.recebidos = [
        titulo(1, "500.00", pagamento=REF, nome="Empresa Dez", descricao="Fatura de setembro"),
        titulo(4, "900.00", pagamento=REF, nome="Empresa Onze", descricao="Fatura"),
        titulo(5, "70.00", pagamento=REF, cancelada=True, nome="Cancelada"),
    ]
    s.financeiro.pagos = [
        titulo(2, "120.00", pagamento=REF, nome="Fornecedor", classificacao="ADMINISTRATIVAS")
    ]
    fluxo = FluxoDoDia(s.financeiro).para(REF).metricas["fluxo_dia"]["linhas"]
    assert [(m["natureza"], m["conta"], m["valor"]) for m in fluxo] == [
        ("receita", "Empresa Onze", 900.0),  # maiores valores primeiro
        ("receita", "Empresa Dez", 500.0),
        ("despesa", "Fornecedor", 120.0),
    ]
    assert fluxo[1]["descricao"] == "Fatura de setembro"
    assert fluxo[2]["classificacao"] == "ADMINISTRATIVAS"
    # A descrição não entra no resumo gravado (LGPD).
    s.consolidar_financeiro.executar(REF)
    assert "Fatura de setembro" not in str(s.banco.resumos_financeiros[REF].metricas)


def test_dto_le_a_descricao_so_em_memoria() -> None:
    from relatorio.infrastructure.sgg.dto_financeiro import para_titulo  # noqa: PLC0415

    t = para_titulo(
        {
            "id": "7",
            "valor": "10,00",
            "descricao": "  Fatura   de\nsetembro ",
            "nome": "Empresa X",
            "cnpj_cpf": "12.345.678/0001-90",
            "centro_de_resultados": "EXAMES",
        }
    )
    assert t.descricao == "Fatura de setembro" and t.classificacao == "EXAMES"
    assert "setembro" not in repr(t)  # nunca aparece em log
