"""Modelos SQLAlchemy (seção 9).

Eventos são imutáveis (só inserção); o resumo diário é recalculável a partir deles.
Além das sete tabelas da documentação, ``coletor_cursor`` guarda o cursor do polling
(RNF04: o coletor retoma de onde parou após reinício).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CONVENCAO_NOMES = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SITUACOES_SQL = "'Agendado','Aguardando','Em Atendimento','Atendido','Faltou','Cancelado'"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCAO_NOMES)
    type_annotation_map = {  # noqa: RUF012 — convenção do SQLAlchemy
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


class AgendaModel(Base):
    __tablename__ = "agenda"

    id_agenda: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    nome: Mapped[str] = mapped_column(String(200))
    sala: Mapped[str | None] = mapped_column(String(200))
    id_unidade_atendimento: Mapped[int | None] = mapped_column(BigInteger)
    unidade_atendimento: Mapped[str | None] = mapped_column(String(200))
    ativa: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    incluir_relatorio: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    atualizado_em: Mapped[datetime] = mapped_column(server_default=func.now())


class AgendamentoSnapshotModel(Base):
    __tablename__ = "agendamento_snapshot"
    __table_args__ = (
        Index("ix_agendamento_snapshot_data_agenda", "data_agendamento", "id_agenda"),
        CheckConstraint(f"situacao_atual IN ({SITUACOES_SQL})", name="situacao_valida"),
    )

    id_agendamento: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    id_agenda: Mapped[int | None] = mapped_column(BigInteger)
    agenda_nome: Mapped[str] = mapped_column(String(200))
    id_unidade_atendimento: Mapped[int | None] = mapped_column(BigInteger)
    data_agendamento: Mapped[date] = mapped_column(Date)
    hora_agendamento: Mapped[time | None] = mapped_column(Time)
    situacao_atual: Mapped[str] = mapped_column(String(20))
    data_hora_edicao: Mapped[datetime | None]
    atualizado_em: Mapped[datetime]


class AgendamentoEventoModel(Base):
    __tablename__ = "agendamento_evento"
    __table_args__ = (
        UniqueConstraint(
            "id_agendamento", "status_novo", "ocorrido_em", name="uq_agendamento_evento_transicao"
        ),
        Index("ix_agendamento_evento_ocorrido_em", "ocorrido_em"),
        Index("ix_agendamento_evento_id_agendamento", "id_agendamento"),
        CheckConstraint(f"status_novo IN ({SITUACOES_SQL})", name="status_novo_valido"),
        CheckConstraint(
            "origem IN ('polling','reconciliacao','verificacao_falta')", name="origem_valida"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    id_agendamento: Mapped[int] = mapped_column(BigInteger)
    status_anterior: Mapped[str | None] = mapped_column(String(20))
    status_novo: Mapped[str] = mapped_column(String(20))
    ocorrido_em: Mapped[datetime]
    observado_em: Mapped[datetime]
    origem: Mapped[str] = mapped_column(String(20))


class ResumoDiarioModel(Base):
    __tablename__ = "resumo_diario"
    __table_args__ = (
        CheckConstraint(
            "status_envio IN ('pendente','enviando','enviado','falha')", name="status_envio_valido"
        ),
    )

    data: Mapped[date] = mapped_column(Date, primary_key=True)
    metricas: Mapped[dict[str, Any]]
    versao_regra: Mapped[str] = mapped_column(String(20))
    gerado_em: Mapped[datetime]
    status_envio: Mapped[str] = mapped_column(String(20), server_default=text("'pendente'"))
    enviado_em: Mapped[datetime | None]


class DestinatarioModel(Base):
    __tablename__ = "destinatario"
    __table_args__ = (UniqueConstraint("email", name="uq_destinatario_email"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    nome: Mapped[str | None] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    criado_em: Mapped[datetime] = mapped_column(server_default=func.now())


class ExecucaoJobModel(Base):
    __tablename__ = "execucao_job"
    __table_args__ = (Index("ix_execucao_job_job_inicio", "job", "inicio"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job: Mapped[str] = mapped_column(String(60))
    inicio: Mapped[datetime]
    fim: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(20))
    detalhe: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class ConfiguracaoModel(Base):
    __tablename__ = "configuracao"

    chave: Mapped[str] = mapped_column(String(80), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(server_default=func.now())


class CursorColetaModel(Base):
    __tablename__ = "coletor_cursor"

    nome: Mapped[str] = mapped_column(String(80), primary_key=True)
    valor: Mapped[datetime]
    atualizado_em: Mapped[datetime] = mapped_column(server_default=func.now())
