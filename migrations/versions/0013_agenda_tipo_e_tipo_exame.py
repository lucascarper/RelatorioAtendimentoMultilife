"""agenda.tipo (sala de exames, consultório ou guichê) e tipo de exame no snapshot

``agenda.guiche`` vira ``agenda.tipo``: guichê continua guichê, o resto começa como
consultório (era assim que o relatório tratava). ``agendamento_snapshot.tipo_exame`` guarda
o campo "tipo" do agendamento no SGG (Admissional, Periódico...) para o detalhamento.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-25 12:00:00-03:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agenda",
        sa.Column(
            "tipo", sa.String(length=20), server_default=sa.text("'consultorio'"), nullable=False
        ),
    )
    op.execute("UPDATE agenda SET tipo = 'guiche' WHERE guiche")
    op.create_check_constraint(
        op.f("ck_agenda_tipo_valido"), "agenda", "tipo IN ('sala_exames','consultorio','guiche')"
    )
    op.drop_column("agenda", "guiche")
    op.add_column(
        "agendamento_snapshot",
        sa.Column("tipo_exame", sa.String(length=60), server_default=sa.text("''"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agendamento_snapshot", "tipo_exame")
    op.add_column(
        "agenda",
        sa.Column("guiche", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute("UPDATE agenda SET guiche = (tipo = 'guiche')")
    op.drop_constraint(op.f("ck_agenda_tipo_valido"), "agenda", type_="check")
    op.drop_column("agenda", "tipo")
