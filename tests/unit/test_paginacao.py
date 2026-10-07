from starlette.requests import Request

from relatorio.interfaces.web.paginacao import PADRAO, paginar


def _req(consulta: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "path": "/admin/x",
            "query_string": consulta.encode(),
            "headers": [],
            "method": "GET",
        }
    )


def test_padrao_e_recorte() -> None:
    pg = paginar(_req(), list(range(60)), "lista")
    assert (pg.tamanho, pg.pagina, pg.paginas, len(pg.itens)) == (PADRAO, 1, 3, 25)
    assert (pg.primeiro, pg.ultimo) == (1, 25)


def test_tamanho_e_pagina_pela_url() -> None:
    pg = paginar(_req("lista_n=10&lista_p=3"), list(range(25)), "lista")
    assert pg.itens == [20, 21, 22, 23, 24]
    assert (pg.primeiro, pg.ultimo) == (21, 25)


def test_valores_invalidos_voltam_ao_padrao() -> None:
    pg = paginar(_req("lista_n=7&lista_p=abc"), list(range(60)), "lista")
    assert (pg.tamanho, pg.pagina) == (PADRAO, 1)
    assert paginar(_req("lista_p=99"), list(range(60)), "lista").pagina == 3


def test_lista_curta_nao_mostra_rodape() -> None:
    assert not paginar(_req(), list(range(10)), "lista").visivel
    assert paginar(_req(), list(range(11)), "lista").visivel


def test_links_preservam_outras_listas() -> None:
    pg = paginar(_req("outra_p=2&lista_n=10"), list(range(40)), "lista")
    assert pg.url(2) == "/admin/x?outra_p=2&lista_n=10&lista_p=2#lista"
    assert pg.url(1, 25) == "/admin/x?outra_p=2#lista"


def test_numeros_com_reticencias() -> None:
    pg = paginar(_req("lista_n=10&lista_p=6"), list(range(200)), "lista")
    assert pg.numeros() == [1, None, 5, 6, 7, None, 20]
