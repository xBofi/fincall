"""Bandeja del administrador: el historial de cada incidencia dice quién actuó y qué hizo.

Las incidencias que no son una reparación clara quedan pendientes del administrador. Desde la
consola las asigna a un especialista, cambia su estado o anota algo; cada acción queda en
`incidencia_historial` con el usuario, el tipo de acción y sus datos (p. ej. que el aviso al
especialista queda pendiente de envío hasta que existan avisos reales).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidencia_historial",
        sa.Column("accion", sa.String(32), nullable=False, server_default="cambiar_estado"),
    )
    op.add_column(
        "incidencia_historial",
        sa.Column(
            "usuario_id",
            sa.String(64),
            sa.ForeignKey(
                "usuarios.id", ondelete="SET NULL", name="fk_incidencia_historial_usuario"
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "incidencia_historial",
        sa.Column(
            "detalle",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_incidencia_historial_usuario", "incidencia_historial", type_="foreignkey"
    )
    for columna in ("detalle", "usuario_id", "accion"):
        op.drop_column("incidencia_historial", columna)
