"""estrutura inicial: tabelas da seção 9 + cursor do coletor + destinatários iniciais

Revision ID: 0001
Revises:
Create Date: 2026-09-24 12:05:12.199096-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agenda",
        sa.Column("id_agenda", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("sala", sa.String(length=200), nullable=True),
        sa.Column("id_unidade_atendimento", sa.BigInteger(), nullable=True),
        sa.Column("unidade_atendimento", sa.String(length=200), nullable=True),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "incluir_relatorio", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id_agenda", name=op.f("pk_agenda")),
    )
    op.create_table(
        "agendamento_evento",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("id_agendamento", sa.BigInteger(), nullable=False),
        sa.Column("status_anterior", sa.String(length=20), nullable=True),
        sa.Column("status_novo", sa.String(length=20), nullable=False),
        sa.Column("ocorrido_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origem", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "origem IN ('polling','reconciliacao','verificacao_falta')",
            name=op.f("ck_agendamento_evento_origem_valida"),
        ),
        sa.CheckConstraint(
            "status_novo IN ('Agendado','Aguardando','Em Atendimento','Atendido','Faltou','Cancelado')",
            name=op.f("ck_agendamento_evento_status_novo_valido"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agendamento_evento")),
        sa.UniqueConstraint(
            "id_agendamento", "status_novo", "ocorrido_em", name="uq_agendamento_evento_transicao"
        ),
    )
    op.create_index(
        "ix_agendamento_evento_id_agendamento",
        "agendamento_evento",
        ["id_agendamento"],
        unique=False,
    )
    op.create_index(
        "ix_agendamento_evento_ocorrido_em", "agendamento_evento", ["ocorrido_em"], unique=False
    )
    op.create_table(
        "agendamento_snapshot",
        sa.Column("id_agendamento", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("id_agenda", sa.BigInteger(), nullable=True),
        sa.Column("agenda_nome", sa.String(length=200), nullable=False),
        sa.Column("id_unidade_atendimento", sa.BigInteger(), nullable=True),
        sa.Column("data_agendamento", sa.Date(), nullable=False),
        sa.Column("hora_agendamento", sa.Time(), nullable=True),
        sa.Column("situacao_atual", sa.String(length=20), nullable=False),
        sa.Column("data_hora_edicao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "situacao_atual IN ('Agendado','Aguardando','Em Atendimento','Atendido','Faltou','Cancelado')",
            name=op.f("ck_agendamento_snapshot_situacao_valida"),
        ),
        sa.PrimaryKeyConstraint("id_agendamento", name=op.f("pk_agendamento_snapshot")),
    )
    op.create_index(
        "ix_agendamento_snapshot_data_agenda",
        "agendamento_snapshot",
        ["data_agendamento", "id_agenda"],
        unique=False,
    )
    op.create_table(
        "coletor_cursor",
        sa.Column("nome", sa.String(length=80), nullable=False),
        sa.Column("valor", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nome", name=op.f("pk_coletor_cursor")),
    )
    op.create_table(
        "configuracao",
        sa.Column("chave", sa.String(length=80), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("chave", name=op.f("pk_configuracao")),
    )
    op.create_table(
        "destinatario",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destinatario")),
        sa.UniqueConstraint("email", name="uq_destinatario_email"),
    )
    op.create_table(
        "execucao_job",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job", sa.String(length=60), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "detalhe",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execucao_job")),
    )
    op.create_index("ix_execucao_job_job_inicio", "execucao_job", ["job", "inicio"], unique=False)
    op.create_table(
        "resumo_diario",
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("metricas", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("versao_regra", sa.String(length=20), nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status_envio",
            sa.String(length=20),
            server_default=sa.text("'pendente'"),
            nullable=False,
        ),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status_envio IN ('pendente','enviando','enviado','falha')",
            name=op.f("ck_resumo_diario_status_envio_valido"),
        ),
        sa.PrimaryKeyConstraint("data", name=op.f("pk_resumo_diario")),
    )

    # Lista inicial de destinatários confirmada na documentação (seção 16).
    destinatario = sa.table(
        "destinatario", sa.column("email", sa.String), sa.column("nome", sa.String)
    )
    op.bulk_insert(
        destinatario,
        [
            {"email": "glauco@multilife.com.br", "nome": "Glauco"},
            {"email": "tecnologia@multilife.com.br", "nome": "Tecnologia MultiLife"},
        ],
    )


def downgrade() -> None:
    op.drop_table("resumo_diario")
    op.drop_index("ix_execucao_job_job_inicio", table_name="execucao_job")
    op.drop_table("execucao_job")
    op.drop_table("destinatario")
    op.drop_table("configuracao")
    op.drop_table("coletor_cursor")
    op.drop_index("ix_agendamento_snapshot_data_agenda", table_name="agendamento_snapshot")
    op.drop_table("agendamento_snapshot")
    op.drop_index("ix_agendamento_evento_ocorrido_em", table_name="agendamento_evento")
    op.drop_index("ix_agendamento_evento_id_agendamento", table_name="agendamento_evento")
    op.drop_table("agendamento_evento")
    op.drop_table("agenda")
