"""planilhas de vencimento do SESMT (PGR, PCMSO, LTCAT) enviadas no admin

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23 12:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planilha_sesmt",
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("arquivo", sa.String(length=200), nullable=False),
        sa.Column("enviada_por", sa.String(length=80), nullable=False),
        sa.Column("enviada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("documentos", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("tipo", name=op.f("pk_planilha_sesmt")),
    )


def downgrade() -> None:
    op.drop_table("planilha_sesmt")
