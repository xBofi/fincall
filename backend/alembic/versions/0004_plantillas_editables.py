"""Plantillas del asistente editables desde la consola.

Solo se guarda lo sobrescrito: una tabla vacía significa "todo como en el código", que es el
estado inicial. El histórico permite ver qué se dijo y volver atrás.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plantillas",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actualizada_por", sa.String(64), nullable=True),
        sa.Column(
            "actualizada_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "plantilla_versiones",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("plantilla_id", sa.String(128), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("autor", sa.String(64), nullable=True),
        sa.Column(
            "creada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("plantilla_versiones")
    op.drop_table("plantillas")
