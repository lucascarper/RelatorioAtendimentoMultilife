"""usuário com e-mail e tokens de definição de senha (esqueci minha senha / enviar acesso)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29 10:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usuario", sa.Column("email", sa.String(length=254), nullable=True))
    op.create_unique_constraint("uq_usuario_email", "usuario", ["email"])
    op.create_table(
        "token_senha",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("id_usuario", sa.Integer(), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["id_usuario"],
            ["usuario.id"],
            name=op.f("fk_token_senha_id_usuario_usuario"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_token_senha")),
    )


def downgrade() -> None:
    op.drop_table("token_senha")
    op.drop_constraint("uq_usuario_email", "usuario", type_="unique")
    op.drop_column("usuario", "email")
