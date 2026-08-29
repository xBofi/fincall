"""Sustituye la identidad de Google por usuarios internos con contraseña.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29

Las filas de `usuarios` y `sesiones` anteriores eran identidades de Google y dejan de tener
sentido: se eliminan. El owner inicial lo crea la API al arrancar (`BC_ADMIN_INICIAL_*`).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM sesiones")
    op.execute("UPDATE auditoria_eventos SET usuario_id = NULL")
    op.execute("DELETE FROM usuarios")

    op.drop_constraint("usuarios_google_sub_key", "usuarios", type_="unique")
    op.drop_constraint("usuarios_email_key", "usuarios", type_="unique")
    op.drop_column("usuarios", "google_sub")
    op.drop_column("usuarios", "email")
    op.add_column("usuarios", sa.Column("nombre_usuario", sa.String(64), nullable=False))
    op.add_column("usuarios", sa.Column("nombre", sa.String(200), nullable=True))
    op.add_column("usuarios", sa.Column("hash_contrasena", sa.String(255), nullable=False))
    op.add_column(
        "usuarios",
        sa.Column(
            "debe_cambiar_contrasena", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "usuarios",
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_usuarios_nombre_usuario", "usuarios", ["nombre_usuario"], unique=True)

    # Baja de usuario: sus sesiones desaparecen; la auditoría conserva el evento sin autor
    op.drop_constraint("sesiones_usuario_id_fkey", "sesiones", type_="foreignkey")
    op.create_foreign_key(
        "sesiones_usuario_id_fkey", "sesiones", "usuarios", ["usuario_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("auditoria_eventos_usuario_id_fkey", "auditoria_eventos", type_="foreignkey")
    op.create_foreign_key(
        "auditoria_eventos_usuario_id_fkey",
        "auditoria_eventos",
        "usuarios",
        ["usuario_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute("DELETE FROM sesiones")
    op.execute("UPDATE auditoria_eventos SET usuario_id = NULL")
    op.execute("DELETE FROM usuarios")
    op.drop_constraint("auditoria_eventos_usuario_id_fkey", "auditoria_eventos", type_="foreignkey")
    op.create_foreign_key(
        "auditoria_eventos_usuario_id_fkey", "auditoria_eventos", "usuarios", ["usuario_id"], ["id"]
    )
    op.drop_constraint("sesiones_usuario_id_fkey", "sesiones", type_="foreignkey")
    op.create_foreign_key("sesiones_usuario_id_fkey", "sesiones", "usuarios", ["usuario_id"], ["id"])
    op.drop_index("ix_usuarios_nombre_usuario", "usuarios")
    op.drop_column("usuarios", "creado_en")
    op.drop_column("usuarios", "debe_cambiar_contrasena")
    op.drop_column("usuarios", "hash_contrasena")
    op.drop_column("usuarios", "nombre")
    op.drop_column("usuarios", "nombre_usuario")
    op.add_column("usuarios", sa.Column("email", sa.String(320), nullable=False))
    op.add_column("usuarios", sa.Column("google_sub", sa.String(255), nullable=False))
    op.create_unique_constraint("usuarios_email_key", "usuarios", ["email"])
    op.create_unique_constraint("usuarios_google_sub_key", "usuarios", ["google_sub"])
