"""exportação: ação (exportar ou processar o período) e opções do pedido

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07 14:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exportacao",
        sa.Column(
            "acao", sa.String(length=10), server_default=sa.text("'exportar'"), nullable=False
        ),
    )
    op.add_column(
        "exportacao",
        sa.Column("opcoes", sa.String(length=100), server_default=sa.text("''"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("exportacao", "opcoes")
    op.drop_column("exportacao", "acao")
