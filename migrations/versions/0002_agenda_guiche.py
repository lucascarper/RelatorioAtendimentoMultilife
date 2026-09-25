"""agenda.guiche: agendas de recepção, separadas na análise por turno

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25 12:40:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agenda",
        sa.Column("guiche", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agenda", "guiche")
