"""exportação: opções do pedido com mais espaço (lista de agendas escolhidas)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09 11:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "exportacao",
        "opcoes",
        existing_type=sa.String(length=100),
        type_=sa.String(length=2000),
        existing_nullable=False,
        existing_server_default=sa.text("''"),
    )


def downgrade() -> None:
    # O limite antigo é menor: corta o que passar dele (só a escolha de agendas se perde).
    op.execute("UPDATE exportacao SET opcoes = left(opcoes, 100)")
    op.alter_column(
        "exportacao",
        "opcoes",
        existing_type=sa.String(length=2000),
        type_=sa.String(length=100),
        existing_nullable=False,
        existing_server_default=sa.text("''"),
    )
