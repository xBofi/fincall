"""Casos del banco de pruebas marcados por calidad desde el expediente de una llamada.

Un turno real del vecino con lo que el asistente debería haber entendido; `evals/exportar_casos.py`
los pasa a `evals/gold_*.jsonl`. Sin teléfono ni nombre del vecino: solo el texto del turno y la
pregunta del bot.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JsonDoc = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "casos_banco",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("llamada_id", sa.String(64), sa.ForeignKey("llamadas.id"), nullable=False),
        sa.Column("turno", sa.Integer(), nullable=False),
        sa.Column("banco", sa.String(32), nullable=False),
        sa.Column("nodo", sa.String(128), nullable=False),
        sa.Column("pregunta", sa.Text(), nullable=True),
        sa.Column("oido", sa.Text(), nullable=True),
        sa.Column("entrada", sa.Text(), nullable=False),
        sa.Column("catalogo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("equipos", JsonDoc, nullable=True),
        sa.Column("esperado", JsonDoc, nullable=False),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column(
            "autor_id",
            sa.String(64),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("llamada_id", "turno", "banco", name="uq_casos_banco_turno"),
    )
    op.create_index("ix_casos_banco_llamada_id", "casos_banco", ["llamada_id"])


def downgrade() -> None:
    op.drop_index("ix_casos_banco_llamada_id", table_name="casos_banco")
    op.drop_table("casos_banco")
