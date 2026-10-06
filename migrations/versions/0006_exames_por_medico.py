"""atendimentos por médico: médicos, exames clínicos e processamentos

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 10:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "medico_relatorio",
        sa.Column("crm", sa.String(length=20), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("selecionado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("visto_em", sa.Date(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("crm", name=op.f("pk_medico_relatorio")),
    )
    op.create_table(
        "exame_clinico",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("id_empresa", sa.Integer(), nullable=False),
        sa.Column("empresa", sa.String(length=200), nullable=False),
        sa.Column("id_funcionario", sa.Integer(), nullable=False),
        sa.Column("crm", sa.String(length=20), nullable=False),
        sa.Column("medico", sa.String(length=200), nullable=False),
        sa.Column("tipo", sa.String(length=60), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exame_clinico")),
    )
    op.create_index(op.f("ix_exame_clinico_data"), "exame_clinico", ["data"])
    op.create_table(
        "coleta_exames",
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("processado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clinicos", sa.Integer(), nullable=False),
        sa.Column("selecionados", sa.Integer(), nullable=False),
        sa.Column(
            "medicos",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("data", name=op.f("pk_coleta_exames")),
    )


def downgrade() -> None:
    op.drop_table("coleta_exames")
    op.drop_index(op.f("ix_exame_clinico_data"), table_name="exame_clinico")
    op.drop_table("exame_clinico")
    op.drop_table("medico_relatorio")
