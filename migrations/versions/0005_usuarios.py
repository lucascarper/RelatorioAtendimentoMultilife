"""usuários do painel e permissões por módulo

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05 10:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usuario",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("usuario", sa.String(length=40), nullable=False),
        sa.Column("senha_hash", sa.String(length=100), nullable=False),
        sa.Column(
            "permissoes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usuario")),
        sa.UniqueConstraint("usuario", name="uq_usuario_usuario"),
    )


def downgrade() -> None:
    op.drop_table("usuario")
