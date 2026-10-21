"""Acceso con Google y Microsoft: identidad del proveedor y estado de la solicitud de acceso.

Las cuentas con contraseña dejan de entrar (decisión de Andreu, 2026-10-21): su hash pasa a ser
opcional y conservan `estado_acceso = concedido` para que el owner las vea y las borre.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("usuarios", "hash_contrasena", existing_type=sa.String(255), nullable=True)
    op.add_column("usuarios", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("usuarios", sa.Column("proveedor", sa.String(32), nullable=True))
    op.add_column("usuarios", sa.Column("sujeto", sa.String(255), nullable=True))
    op.add_column(
        "usuarios",
        sa.Column("estado_acceso", sa.String(32), nullable=False, server_default="concedido"),
    )
    op.add_column("usuarios", sa.Column("solicitado_en", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_usuarios_email", "usuarios", ["email"])
    op.create_unique_constraint("uq_usuarios_identidad", "usuarios", ["proveedor", "sujeto"])


def downgrade() -> None:
    op.drop_constraint("uq_usuarios_identidad", "usuarios", type_="unique")
    op.drop_index("ix_usuarios_email", table_name="usuarios")
    for columna in ("solicitado_en", "estado_acceso", "sujeto", "proveedor", "email"):
        op.drop_column("usuarios", columna)
    op.alter_column("usuarios", "hash_contrasena", existing_type=sa.String(255), nullable=False)
