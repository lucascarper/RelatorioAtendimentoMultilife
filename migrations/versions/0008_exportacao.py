"""exportação de relatórios por período (progresso e arquivo para download)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07 10:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exportacao",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("inicio", sa.Date(), nullable=False),
        sa.Column("fim", sa.Date(), nullable=False),
        sa.Column("solicitante", sa.String(length=40), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("progresso", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("etapa", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "nome_arquivo", sa.String(length=120), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("erro", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("arquivo", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exportacao")),
    )
    op.create_index(op.f("ix_exportacao_criado_em"), "exportacao", ["criado_em"])


def downgrade() -> None:
    op.drop_index(op.f("ix_exportacao_criado_em"), table_name="exportacao")
    op.drop_table("exportacao")
