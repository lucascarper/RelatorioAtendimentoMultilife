"""Portas (interfaces) da aplicação — implementadas pela infraestrutura.

A aplicação depende apenas destes contratos; trocar o banco, o cliente HTTP ou o
provedor de e-mail não muda nenhum caso de uso (inversão de dependência).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Protocol, Self

from relatorio.application.modelos import (
    ConteudoEmail,
    Destinatario,
    ExecucaoJob,
    ResumoRegistro,
    StatusJob,
)
from relatorio.domain.entidades import Agenda, AgendamentoSgg, Evento, Situacao, Snapshot

# --------------------------------------------------------------------------- externos


class ErroIntegracao(Exception):
    """Falha de um serviço externo (SGG, SMTP). As implementações herdam desta classe."""


class SggGateway(Protocol):
    """Leitura da API do SGG (somente GET)."""

    @property
    def requisicoes_realizadas(self) -> int: ...

    def agendamentos_editados(self, de: datetime, ate: datetime) -> list[AgendamentoSgg]: ...

    def agendamentos_do_dia(
        self, dia: date, situacao: Situacao | None = None
    ) -> list[AgendamentoSgg]: ...

    def agendas(self) -> list[Agenda]: ...


class EnviadorEmail(Protocol):
    def enviar(self, destinatarios: Sequence[str], conteudo: ConteudoEmail) -> None: ...


class RenderizadorEmail(Protocol):
    def relatorio(self, metricas: Mapping[str, Any]) -> ConteudoEmail: ...

    def alerta(self, titulo: str, mensagem: str, detalhes: Mapping[str, str]) -> ConteudoEmail: ...


class Relogio(Protocol):
    def agora(self) -> datetime:
        """Instante atual com fuso (nunca ``datetime.now()`` sem fuso)."""
        ...


# --------------------------------------------------------------------------- repositórios


class AgendaRepository(Protocol):
    def listar(self) -> list[Agenda]: ...

    def sincronizar(self, agendas: Sequence[Agenda]) -> tuple[int, int, int]:
        """Upsert preservando ``incluir_relatorio`` e ``guiche``; desativa as que sumiram.

        Retorna (novas, atualizadas, desativadas).
        """
        ...

    def definir_inclusao(self, id_agenda: int, incluir: bool) -> None: ...

    def definir_guiche(self, id_agenda: int, guiche: bool) -> None: ...


class SnapshotRepository(Protocol):
    def obter_varios(self, ids: Collection[int]) -> dict[int, Snapshot]: ...

    def salvar(self, snapshot: Snapshot) -> None: ...

    def listar_por_data(self, inicio: date, fim: date) -> list[Snapshot]: ...

    def apagar_anteriores_a(self, limite: date) -> int: ...


class EventoRepository(Protocol):
    def inserir(self, evento: Evento) -> bool:
        """Insere o evento; devolve False se ele já existia (idempotência)."""
        ...

    def listar_por_agendamentos(self, ids: Collection[int]) -> dict[int, list[Evento]]: ...

    def apagar_anteriores_a(self, limite: datetime) -> int: ...


class ResumoRepository(Protocol):
    def obter(self, dia: date) -> ResumoRegistro | None: ...

    def salvar_metricas(
        self, dia: date, metricas: Mapping[str, Any], versao_regra: str, gerado_em: datetime
    ) -> None:
        """Grava/atualiza as métricas sem mexer no status de envio de um dia já enviado."""
        ...

    def reservar_envio(self, dia: date, forcar: bool = False) -> bool:
        """Marca o dia como "enviando" se ainda não foi enviado (ou se ``forcar``).

        É a trava que impede e-mail duplicado quando o envio é reexecutado.
        """
        ...

    def marcar_enviado(self, dia: date, quando: datetime) -> None: ...

    def marcar_falha_envio(self, dia: date) -> None: ...

    def listar_recentes(self, limite: int = 30) -> list[ResumoRegistro]: ...

    def apagar_anteriores_a(self, limite: date) -> int: ...


class DestinatarioRepository(Protocol):
    def listar(self, apenas_ativos: bool = False) -> list[Destinatario]: ...

    def adicionar(self, email: str, nome: str | None) -> Destinatario: ...

    def definir_ativo(self, id_destinatario: int, ativo: bool) -> None: ...


class ConfiguracaoRepository(Protocol):
    def obter_todas(self) -> dict[str, str]: ...

    def definir(self, chave: str, valor: str) -> None: ...


class ExecucaoJobRepository(Protocol):
    def iniciar(self, job: str, inicio: datetime, detalhe: Mapping[str, Any]) -> int: ...

    def finalizar(
        self, id_execucao: int, fim: datetime, status: StatusJob, detalhe: Mapping[str, Any]
    ) -> None: ...

    def recentes_por_job(self, por_job: int = 7) -> dict[str, list[ExecucaoJob]]: ...

    def ultima(self, job: str, status: StatusJob | None = None) -> ExecucaoJob | None: ...

    def inicios_com_sucesso(self, job: str, de: datetime, ate: datetime) -> list[datetime]: ...

    def houve_sucesso(self, job: str, referencia: str) -> bool:
        """Já existe execução bem-sucedida de ``job`` para a referência (ex.: a data)?"""
        ...

    def falhas_consecutivas(self, job: str) -> int: ...

    def compactar_sucessos(self, job: str, anteriores_a: datetime) -> int:
        """Apaga sucessos de ``job`` antes de ``anteriores_a``, menos o 1º de cada minuto."""
        ...

    def apagar_anteriores_a(self, limite: datetime) -> int: ...


class CursorRepository(Protocol):
    def obter(self, nome: str) -> datetime | None: ...

    def salvar(self, nome: str, valor: datetime) -> None: ...


class UnidadeDeTrabalho(Protocol):
    """Transação única com todos os repositórios. Sem ``commit`` explícito, desfaz."""

    @property
    def agendas(self) -> AgendaRepository: ...

    @property
    def snapshots(self) -> SnapshotRepository: ...

    @property
    def eventos(self) -> EventoRepository: ...

    @property
    def resumos(self) -> ResumoRepository: ...

    @property
    def destinatarios(self) -> DestinatarioRepository: ...

    @property
    def configuracoes(self) -> ConfiguracaoRepository: ...

    @property
    def execucoes(self) -> ExecucaoJobRepository: ...

    @property
    def cursores(self) -> CursorRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


FabricaUoW = Callable[[], UnidadeDeTrabalho]
