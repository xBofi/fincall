"""Inventario de equipos por finca y a quién se avisa de cada incidencia.

Cada finca puede listar sus equipos (portero, ascensor, puerta de garaje...) con marca, modelo,
instalador y mantenedor: se avisa al mantenedor antes que a un profesional genérico. La
incidencia guarda el equipo, el tipo de actuación y la sugerencia para el administrador (regla de
producto, 2026-10-21).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipos",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("finca_id", sa.String(64), sa.ForeignKey("fincas.id"), nullable=False),
        sa.Column("tipo", sa.String(120), nullable=False),
        sa.Column("ubicacion", sa.String(200), nullable=True),
        sa.Column("marca", sa.String(120), nullable=True),
        sa.Column("modelo", sa.String(120), nullable=True),
        sa.Column("instalador", sa.String(200), nullable=True),
        sa.Column("mantenedor_id", sa.String(64), sa.ForeignKey("especialistas.id"), nullable=True),
        sa.Column("contrato", sa.String(120), nullable=True),
        sa.Column("garantia_hasta", sa.Date(), nullable=True),
    )
    op.create_index("ix_equipos_finca_id", "equipos", ["finca_id"])
    op.add_column(
        "incidencias",
        sa.Column("equipo_id", sa.String(64), sa.ForeignKey("equipos.id"), nullable=True),
    )
    op.add_column(
        "incidencias",
        sa.Column("actuacion", sa.String(32), nullable=False, server_default="reparacion"),
    )
    op.add_column("incidencias", sa.Column("sugerencia", sa.Text(), nullable=True))


def downgrade() -> None:
    for columna in ("sugerencia", "actuacion", "equipo_id"):
        op.drop_column("incidencias", columna)
    op.drop_index("ix_equipos_finca_id", table_name="equipos")
    op.drop_table("equipos")
