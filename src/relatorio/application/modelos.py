"""Modelos da camada de aplicação (registros persistidos e mensagens)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class StatusEnvio(StrEnum):
    PENDENTE = "pendente"
    ENVIANDO = "enviando"
    ENVIADO = "enviado"
    FALHA = "falha"


class StatusJob(StrEnum):
    EXECUTANDO = "executando"
    SUCESSO = "sucesso"
    FALHA = "falha"
    IGNORADO = "ignorado"


@dataclass(frozen=True, slots=True)
class ResumoRegistro:
    data: date
    metricas: dict[str, Any]
    versao_regra: str
    gerado_em: datetime
    status_envio: StatusEnvio
    enviado_em: datetime | None


@dataclass(frozen=True, slots=True)
class Destinatario:
    id: int
    email: str
    nome: str | None
    ativo: bool
    criado_em: datetime


@dataclass(frozen=True, slots=True)
class ExecucaoJob:
    id: int
    job: str
    inicio: datetime
    fim: datetime | None
    status: StatusJob
    detalhe: dict[str, Any] = field(default_factory=dict)

    @property
    def duracao_ms(self) -> int | None:
        if self.fim is None:
            return None
        return round((self.fim - self.inicio).total_seconds() * 1000)


@dataclass(frozen=True, slots=True)
class ImagemInline:
    cid: str
    conteudo: bytes
    subtipo: str = "png"


@dataclass(frozen=True, slots=True)
class ConteudoEmail:
    assunto: str
    html: str
    texto: str
    imagens: tuple[ImagemInline, ...] = ()
