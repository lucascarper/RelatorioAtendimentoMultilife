"""Cliente HTTP do SGG contra respostas simuladas (respx)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from relatorio.domain.entidades import FUSO_BRASILIA, Situacao
from relatorio.infrastructure.sgg.cliente import ClienteSgg
from relatorio.infrastructure.sgg.dto import para_agendamento
from relatorio.infrastructure.sgg.erros import (
    ErroSgg,
    ErroSggConfiguracao,
    ErroSggLimite,
    ErroSggRequisicao,
    ErroSggServidor,
)
from relatorio.infrastructure.sgg.limitador import LimitadorTaxa
from relatorio.interfaces.spike import executar_spike

BASE = "https://app.sgg.net.br/api/v3/"
CHAVE = "CHAVEDETESTE0123456789abcdefABCD"
FIXTURES = Path(__file__).parent.parent / "fixtures"


def carregar(nome: str) -> dict[str, Any]:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=FUSO_BRASILIA)


@pytest.fixture
def esperas() -> list[float]:
    return []


@pytest.fixture
def cliente(esperas: list[float]) -> ClienteSgg:
    # Limitador folgado: a paginação de teste faz mais de 100 chamadas sem esperar.
    return ClienteSgg(
        BASE.rstrip("/"), CHAVE, limitador=LimitadorTaxa(10_000), dormir=esperas.append
    )


@respx.mock
def test_autenticacao_barra_final_e_filtros(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(200, json={"resultado": [], "temProximaPagina": False})
    )
    cliente.agendamentos_editados(instante("2026-09-23 07:58:00"), instante("2026-09-23 08:01:00"))
    requisicao = rota.calls.last.request
    esperado = base64.b64encode(f"{CHAVE}:".encode()).decode()
    assert requisicao.headers["Authorization"] == f"Basic {esperado}"
    assert str(requisicao.url).startswith(BASE + "agendamento/?")
    params = requisicao.url.params
    assert params["editado_aPartirDe"] == "2026-09-23 07:58:00"
    assert params["editado_ate"] == "2026-09-23 08:01:00"
    # AG001: o SGG exige um filtro obrigatório junto com editado_* (período de agendamento).
    assert params["data_hora_agendamento_aPartirDe"] == "2026-09-23 00:00:00"
    assert params["data_hora_agendamento_ate"] == "2026-09-23 23:59:59"
    assert params["paginador[pagina]"] == "0"
    assert params["paginador[tamanho]"] == "100"
    assert cliente.requisicoes_realizadas == 1


@respx.mock
def test_filtro_convertido_para_horario_de_brasilia(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(200, json={"resultado": []})
    )
    ate = datetime(2026, 9, 23, 11, 0, tzinfo=UTC)  # 08:00 em Brasília
    cliente.agendamentos_editados(ate - timedelta(minutes=5), ate)
    assert rota.calls.last.request.url.params["editado_ate"] == "2026-09-23 08:00:00"


@respx.mock
def test_janela_de_edicao_longa_respeita_limite_de_um_mes(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(200, json={"resultado": []})
    )
    cliente.agendamentos_editados(instante("2026-08-01 23:00:00"), instante("2026-09-23 08:00:00"))
    for chamada in rota.calls:
        p = chamada.request.url.params
        de = datetime.fromisoformat(p["data_hora_agendamento_aPartirDe"])
        ate = datetime.fromisoformat(p["data_hora_agendamento_ate"])
        assert ate - de < timedelta(days=31)  # AG009
        assert p["data_hora_agendamento_aPartirDe"][:10] == p["editado_aPartirDe"][:10]
        assert p["data_hora_agendamento_ate"][:10] == p["editado_ate"][:10]


@respx.mock
def test_paginacao_segue_tem_proxima_pagina_e_descarta_invalidos(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        side_effect=[
            httpx.Response(200, json=carregar("agendamento_pagina0.json")),
            httpx.Response(200, json=carregar("agendamento_pagina1.json")),
        ]
    )
    registros = cliente.agendamentos_do_dia(date(2026, 9, 23))
    assert [r.id_agendamento for r in registros] == [1001, 1002, 1003]  # 1004 é inválido
    assert [c.request.url.params["paginador[pagina]"] for c in rota.calls] == ["0", "1"]
    params = rota.calls[0].request.url.params
    assert params["data_hora_agendamento_aPartirDe"] == "2026-09-23 00:00:00"
    assert params["data_hora_agendamento_ate"] == "2026-09-23 23:59:59"
    primeiro = registros[0]
    assert primeiro.agenda_nome == "Clínico Geral"
    assert primeiro.situacao is Situacao.EM_ATENDIMENTO
    assert primeiro.hora_agendamento == time(8, 0)
    assert primeiro.data_hora_edicao == instante("2026-09-23 08:05:12")
    assert registros[1].hora_agendamento == time(13, 30)
    assert registros[2].hora_agendamento is None
    assert registros[2].id_unidade_atendimento == 1


def test_campos_pessoais_nao_chegam_ao_dominio() -> None:
    item = carregar("agendamento_pagina0.json")["resultado"][0]
    registro = para_agendamento(item)
    serializado = repr(registro)
    for proibido in ("999001", "1900-01-01", "SETOR", "CARGO", "fictício que"):
        assert proibido not in serializado


@respx.mock
def test_filtro_por_situacao(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(200, json={"resultado": [], "temProximaPagina": False})
    )
    cliente.agendamentos_do_dia(date(2026, 9, 23), situacao=Situacao.FALTOU)
    assert rota.calls.last.request.url.params["situacao"] == "Faltou"


@respx.mock
def test_resultado_unico_como_objeto(cliente: ClienteSgg) -> None:
    item = carregar("agendamento_pagina0.json")["resultado"][0]
    respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(200, json={"resultado": item, "temProximaPagina": "false"})
    )
    assert len(cliente.agendamentos_do_dia(date(2026, 9, 23))) == 1


@respx.mock
def test_d001_retorno_em_branco_e_sucesso_sem_dados(cliente: ClienteSgg) -> None:
    respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(
            400,
            json={"erro": "D001", "msg": "Nenhum problema ocorrido. Porém o retorno é em branco."},
        )
    )
    assert cliente.agendamentos_do_dia(date(2026, 9, 23)) == []


@respx.mock
def test_d001_no_formato_status_code(cliente: ClienteSgg) -> None:
    respx.get(BASE + "agenda/").mock(
        return_value=httpx.Response(200, json={"statusCode": "D001", "statusMsg": "vazio"})
    )
    assert cliente.agendas() == []


@respx.mock
def test_429_com_backoff_exponencial(cliente: ClienteSgg, esperas: list[float]) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, json={"resultado": [], "temProximaPagina": False}),
        ]
    )
    assert cliente.agendamentos_do_dia(date(2026, 9, 23)) == []
    assert rota.call_count == 4
    assert esperas == [2.0, 4.0, 30.0]


@respx.mock
def test_429_persistente_desiste(cliente: ClienteSgg, esperas: list[float]) -> None:
    respx.get(BASE + "agendamento/").mock(return_value=httpx.Response(429))
    with pytest.raises(ErroSggLimite):
        cliente.agendamentos_do_dia(date(2026, 9, 23))
    assert esperas == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0]


@pytest.mark.parametrize(
    ("status", "corpo", "erro"),
    [
        (400, {"erro": "S000", "msg": "Erro interno do Servidor."}, ErroSggServidor),
        (400, {"erro": "E000", "msg": "Problema interno"}, ErroSggServidor),
        (502, "<html>Bad gateway</html>", ErroSggServidor),
        (400, {"erro": "A001", "msg": "Chave de API não encontrada."}, ErroSggConfiguracao),
        (400, {"erro": "AG009", "msg": "Intervalo maior que 1 mês"}, ErroSggRequisicao),
        (200, {"erro": "D012", "msg": "JSON incorreto"}, ErroSggRequisicao),
        (200, ["lista"], ErroSggServidor),
    ],
)
@respx.mock
def test_codigos_de_erro(
    cliente: ClienteSgg, status: int, corpo: object, erro: type[ErroSgg]
) -> None:
    resposta = (
        httpx.Response(status, text=corpo)
        if isinstance(corpo, str)
        else httpx.Response(status, json=corpo)
    )
    respx.get(BASE + "agendamento/").mock(return_value=resposta)
    with pytest.raises(erro):
        cliente.agendamentos_do_dia(date(2026, 9, 23))


@respx.mock
def test_timeout_vira_erro_de_servidor(cliente: ClienteSgg) -> None:
    respx.get(BASE + "agenda/").mock(side_effect=httpx.ConnectTimeout("lento"))
    with pytest.raises(ErroSggServidor, match="ConnectTimeout"):
        cliente.agendas()


def test_sem_chave_nao_faz_requisicao() -> None:
    cliente = ClienteSgg(BASE, "")
    with pytest.raises(ErroSggConfiguracao, match="SGG_API_KEY"):
        cliente.agendas()
    assert cliente.requisicoes_realizadas == 0


@respx.mock
def test_agendas(cliente: ClienteSgg) -> None:
    respx.get(BASE + "agenda/").mock(return_value=httpx.Response(200, json=carregar("agenda.json")))
    agendas = cliente.agendas()
    assert [(a.id_agenda, a.consultorio, a.ativa) for a in agendas] == [
        (32, "Consultório 01", True),
        (33, "Audiometria", False),
    ]
    assert agendas[0].unidade_atendimento == "Unidade Centro"


@respx.mock
def test_intervalo_grande_e_dividido_em_consultas_de_30_dias(cliente: ClienteSgg) -> None:
    rota = respx.get(BASE + "agendamento/").mock(
        return_value=httpx.Response(200, json={"resultado": [], "temProximaPagina": False})
    )
    fim = instante("2026-09-23 08:00:00")
    cliente.agendamentos_editados(fim - timedelta(days=45), fim)
    assert rota.call_count == 2


@respx.mock
def test_registro_repetido_entre_paginas_mantem_edicao_mais_recente(cliente: ClienteSgg) -> None:
    item = carregar("agendamento_pagina0.json")["resultado"][0]
    novo = {**item, "situacao": "Atendido", "data_hora_edicao": "2026-09-23 08:20:00"}
    respx.get(BASE + "agendamento/").mock(
        side_effect=[
            httpx.Response(200, json={"resultado": [novo], "temProximaPagina": True}),
            httpx.Response(200, json={"resultado": [item], "temProximaPagina": False}),
        ]
    )
    [registro] = cliente.agendamentos_do_dia(date(2026, 9, 23))
    assert registro.situacao is Situacao.ATENDIDO


@respx.mock
def test_paginacao_infinita_e_interrompida(cliente: ClienteSgg) -> None:
    respx.get(BASE + "agenda/").mock(
        return_value=httpx.Response(200, json={"resultado": [], "temProximaPagina": True})
    )
    with pytest.raises(ErroSgg, match="Paginação"):
        cliente.agendas()


@respx.mock
def test_spike_so_imprime_agregados(cliente: ClienteSgg) -> None:
    respx.get(BASE + "agenda/").mock(return_value=httpx.Response(200, json=carregar("agenda.json")))
    respx.get(BASE + "agendamento/").mock(
        side_effect=[
            httpx.Response(200, json=carregar("agendamento_pagina0.json")),
            httpx.Response(200, json=carregar("agendamento_pagina1.json")),
            httpx.Response(200, json={"resultado": [], "temProximaPagina": False}),
        ]
    )
    relatorio = executar_spike(cliente, date(2026, 9, 23), instante("2026-09-23 14:00:00"))
    assert relatorio["autenticacao"] == "ok"
    assert relatorio["agendas"]["total"] == 2
    dia = relatorio["agendamentos_do_dia"]
    assert dia["total"] == 3
    assert dia["paginas_de_100"] == 2
    assert dia["por_situacao"] == {"Em Atendimento": 1, "Aguardando": 1, "Faltou": 1}
    assert dia["agendas_sem_cadastro"] == []
    assert relatorio["faltas_aparecem_em_agendamento"] is True
    texto = json.dumps(relatorio, ensure_ascii=False)
    assert "999001" not in texto  # nenhum id de funcionário
    assert "1900-01-01" not in texto  # nenhuma data de nascimento


def test_spike_sem_chave(esperas: list[float]) -> None:
    cliente = ClienteSgg(BASE, "")
    assert executar_spike(cliente, date(2026, 9, 23), instante("2026-09-23 14:00:00"))[
        "autenticacao"
    ].startswith("FALHOU")


class TestLimitador:
    def test_respeita_orcamento_por_minuto(self) -> None:
        agora = [0.0]
        esperas: list[float] = []

        def dormir(segundos: float) -> None:
            esperas.append(segundos)
            agora[0] += segundos

        limitador = LimitadorTaxa(3, relogio=lambda: agora[0], dormir=dormir)
        for _ in range(3):
            limitador.aguardar()
            agora[0] += 1
        limitador.aguardar()
        assert esperas == [57.0]

    def test_valor_invalido(self) -> None:
        with pytest.raises(ValueError, match="≥ 1"):
            LimitadorTaxa(0)


class TestDto:
    def test_datas_vazias_e_horas(self) -> None:
        item = {
            "id_agendamento": "5",
            "agenda": "",
            "data_agendamento": "2026-09-23",
            "hora_agendamento": "10:30",
            "situacao": "Agendado",
            "data_hora_edicao": "0000-00-00 00:00:00",
            "id_unidade_atendimento": "abc",
        }
        registro = para_agendamento(item)
        assert registro.data_hora_edicao is None
        assert registro.agenda_nome == "Agenda sem nome"
        assert registro.id_unidade_atendimento is None

    def test_sem_campos_obrigatorios(self) -> None:
        with pytest.raises(ValueError, match="sem id"):
            para_agendamento({"agenda": "X"})
