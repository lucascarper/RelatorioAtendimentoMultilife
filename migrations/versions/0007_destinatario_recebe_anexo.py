"""destinatários do relatório de atendimentos: quem recebe a planilha nominal

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 18:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ninguém recebe a planilha até ser marcado no admin (LGPD: autorização explícita).
    op.add_column(
        "destinatario",
        sa.Column("recebe_anexo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("destinatario", "recebe_anexo")
