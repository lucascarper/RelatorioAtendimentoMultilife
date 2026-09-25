"""Cliente HTTP da API do SGG v3 (somente leitura).

Regras da seção 10:

* Basic Auth com a chave como usuário e senha vazia; base URL sempre com barra final;
* filtros como parâmetros de query; paginação por ``paginador[pagina]``/``[tamanho]``
  seguindo ``temProximaPagina`` (tamanho máximo 100);
* ``D001`` (retorno em branco) é sucesso sem dados;
* HTTP 429 → espera e tenta de novo com backoff exponencial (2 s, 4 s, 8 s… até 60 s);
* erros ``S0xx``/``E000`` → ``ErroSggServidor`` (o ciclo é pulado e registrado);
* timeout de 15 s e um único ``httpx.Client`` reutilizado (pool de conexões).
"""

from __future__ import annotations

import time as time_module
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx
import structlog
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from relatorio.domain.entidades import FUSO_BRASILIA, Agenda, AgendamentoSgg, Situacao
from relatorio.infrastructure.sgg.dto import formatar_filtro, para_agenda, para_agendamento
from relatorio.infrastructure.sgg.erros import (
    ErroSgg,
    ErroSggConfiguracao,
    ErroSggLimite,
    ErroSggRequisicao,
    ErroSggServidor,
)
from relatorio.infrastructure.sgg.limitador import LimitadorTaxa

log = structlog.get_logger(__name__)

TAMANHO_PAGINA = 100
MAX_PAGINAS = 100  # D028: a API não aceita página maior que o tamanho
MAX_TENTATIVAS_429 = 7
# AG025/AG009: no máximo 1 mês por consulta. O período de agendamento enviado junto
# (dias inteiros) é um pouco maior que a janela de edição, por isso a folga.
INTERVALO_MAXIMO_FILTRO = timedelta(days=27)
CODIGOS_SEM_DADOS = {"D001"}
CODIGOS_AUTENTICACAO = {"A000", "A001", "S002", "S006"}


def _espera_429(estado: RetryCallState) -> float:
    exponencial = min(60.0, 2.0**estado.attempt_number)
    erro = estado.outcome.exception() if estado.outcome else None
    if isinstance(erro, ErroSggLimite) and erro.retry_after:
        return min(60.0, max(exponencial, erro.retry_after))
    return exponencial


_EDICAO_MINIMA = datetime.min.replace(tzinfo=FUSO_BRASILIA)


def _edicao(registro: AgendamentoSgg) -> datetime:
    return registro.data_hora_edicao or _EDICAO_MINIMA


def _verdadeiro(valor: Any) -> bool:
    if isinstance(valor, str):
        return valor.strip().lower() in {"true", "1", "sim", "s"}
    return bool(valor)


class ClienteSgg:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        max_rpm: int = 20,
        timeout_s: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        limitador: LimitadorTaxa | None = None,
        dormir: Callable[[float], None] | None = None,
    ) -> None:
        if not base_url.endswith("/"):
            base_url += "/"  # a API exige a barra no final
        self._api_key = api_key
        self._http = httpx.Client(
            base_url=base_url,
            auth=httpx.BasicAuth(api_key, ""),
            timeout=timeout_s,
            headers={
                "Accept": "application/json",
                "User-Agent": "MultiLife-RelatorioAtendimentos/1.0",
            },
            transport=transport,
        )
        self._limitador = limitador or LimitadorTaxa(max_rpm)
        self._dormir: Callable[[float], None] = dormir or time_module.sleep
        self._requisicoes = 0

    @property
    def requisicoes_realizadas(self) -> int:
        return self._requisicoes

    def fechar(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ HTTP

    def _requisitar(self, caminho: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        if not self._api_key:
            raise ErroSggConfiguracao("SGG_API_KEY não configurada")
        self._limitador.aguardar()
        self._requisicoes += 1
        try:
            resposta = self._http.get(caminho, params=dict(params))
        except httpx.TransportError as erro:
            raise ErroSggServidor(f"Falha de conexão com o SGG: {type(erro).__name__}") from erro

        if resposta.status_code == 429:
            retry_after = resposta.headers.get("Retry-After")
            log.warning("sgg_limite_429", caminho=caminho, retry_after=retry_after)
            raise ErroSggLimite(float(retry_after) if retry_after else None)

        try:
            corpo = resposta.json()
        except ValueError as erro:
            raise ErroSggServidor(
                f"Resposta não-JSON do SGG (HTTP {resposta.status_code})"
            ) from erro
        if not isinstance(corpo, dict):
            raise ErroSggServidor("Resposta inesperada do SGG (JSON não é objeto)")

        codigo = str(corpo.get("erro") or corpo.get("statusCode") or "").strip() or None
        mensagem = str(corpo.get("msg") or corpo.get("statusMsg") or "")
        if codigo in CODIGOS_SEM_DADOS:
            return {"resultado": [], "temProximaPagina": False}
        if codigo in CODIGOS_AUTENTICACAO:
            raise ErroSggConfiguracao(mensagem or "Acesso negado pelo SGG", codigo)
        if resposta.status_code >= 500 or (
            codigo is not None and (codigo.startswith("S") or codigo == "E000")
        ):
            raise ErroSggServidor(mensagem or f"HTTP {resposta.status_code}", codigo)
        if resposta.status_code >= 400 or (
            codigo not in (None, "D000") and "resultado" not in corpo
        ):
            raise ErroSggRequisicao(mensagem or f"HTTP {resposta.status_code}", codigo)
        return corpo

    def _get(self, caminho: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        tentativas = Retrying(
            retry=retry_if_exception_type(ErroSggLimite),
            wait=_espera_429,
            stop=stop_after_attempt(MAX_TENTATIVAS_429),
            reraise=True,
            sleep=self._dormir,
        )
        return tentativas(self._requisitar, caminho, params)

    def _paginar(self, caminho: str, filtros: Mapping[str, str]) -> Iterator[dict[str, Any]]:
        for pagina in range(MAX_PAGINAS + 1):
            corpo = self._get(
                caminho,
                {
                    **filtros,
                    "paginador[pagina]": pagina,
                    "paginador[tamanho]": TAMANHO_PAGINA,
                },
            )
            resultado = corpo.get("resultado")
            if isinstance(resultado, dict):
                resultado = [resultado]
            for item in resultado or []:
                if isinstance(item, dict):
                    yield item
            if not _verdadeiro(corpo.get("temProximaPagina")):
                return
        raise ErroSgg(f"Paginação de {caminho} passou de {MAX_PAGINAS} páginas")

    # ------------------------------------------------------------------ SggGateway

    def _agendamentos(self, filtros: Mapping[str, str]) -> list[AgendamentoSgg]:
        registros: dict[int, AgendamentoSgg] = {}
        for item in self._paginar("agendamento/", filtros):
            try:
                registro = para_agendamento(item)
            except ValueError as erro:
                # Nunca registra o item bruto: ele contém dados pessoais.
                log.warning(
                    "sgg_agendamento_ignorado", id=item.get("id_agendamento"), motivo=str(erro)
                )
                continue
            # A mesma linha pode aparecer em duas páginas se a lista mudar no meio da
            # paginação; fica a versão com a edição mais recente.
            atual = registros.get(registro.id_agendamento)
            if atual is None or _edicao(registro) >= _edicao(atual):
                registros[registro.id_agendamento] = registro
        return list(registros.values())

    def agendamentos_editados(self, de: datetime, ate: datetime) -> list[AgendamentoSgg]:
        """Agendamentos editados em ``[de, ate]`` e marcados para os dias dessa janela.

        O SGG não aceita ``editado_*`` sozinho (AG001): exige também um filtro como o
        período de agendamento. Enviamos os dias inteiros cobertos pela janela, que
        são os agendamentos que entram no relatório desses dias.
        """
        resultado: list[AgendamentoSgg] = []
        inicio = de
        while inicio < ate:
            fim = min(ate, inicio + INTERVALO_MAXIMO_FILTRO)
            primeiro_dia = inicio.astimezone(FUSO_BRASILIA).date()
            ultimo_dia = fim.astimezone(FUSO_BRASILIA).date()
            resultado.extend(
                self._agendamentos(
                    {
                        "editado_aPartirDe": formatar_filtro(inicio),
                        "editado_ate": formatar_filtro(fim),
                        "data_hora_agendamento_aPartirDe": formatar_filtro(
                            datetime.combine(primeiro_dia, time.min, tzinfo=FUSO_BRASILIA)
                        ),
                        "data_hora_agendamento_ate": formatar_filtro(
                            datetime.combine(ultimo_dia, time(23, 59, 59), tzinfo=FUSO_BRASILIA)
                        ),
                    }
                )
            )
            inicio = fim
        return resultado

    def agendamentos_do_dia(
        self, dia: date, situacao: Situacao | None = None
    ) -> list[AgendamentoSgg]:
        filtros = {
            "data_hora_agendamento_aPartirDe": formatar_filtro(
                datetime.combine(dia, time.min, tzinfo=FUSO_BRASILIA)
            ),
            "data_hora_agendamento_ate": formatar_filtro(
                datetime.combine(dia, time(23, 59, 59), tzinfo=FUSO_BRASILIA)
            ),
        }
        if situacao is not None:
            filtros["situacao"] = situacao.value
        return self._agendamentos(filtros)

    def agendas(self) -> list[Agenda]:
        agendas: list[Agenda] = []
        for item in self._paginar("agenda/", {}):
            try:
                agendas.append(para_agenda(item))
            except ValueError as erro:
                log.warning("sgg_agenda_ignorada", motivo=str(erro))
        return agendas
