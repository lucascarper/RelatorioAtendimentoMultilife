"""relatório financeiro: resumo diário e lista própria de destinatários

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28 10:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumo_financeiro",
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
            name=op.f("ck_resumo_financeiro_status_envio_valido"),
        ),
        sa.PrimaryKeyConstraint("data", name=op.f("pk_resumo_financeiro")),
    )
    op.create_table(
        "destinatario_financeiro",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=True),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destinatario_financeiro")),
        sa.UniqueConstraint("email", name="uq_destinatario_financeiro_email"),
    )


def downgrade() -> None:
    op.drop_table("destinatario_financeiro")
    op.drop_table("resumo_financeiro")
