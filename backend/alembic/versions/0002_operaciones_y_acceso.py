"""Añade acceso, auditoría y detalle operativo de llamadas.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llamadas", sa.Column("estado", sa.String(32), nullable=False, server_default="activa"))
    op.add_column("llamadas", sa.Column("finalizada_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("llamadas", sa.Column("requiere_revision", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_llamadas_estado_iniciada", "llamadas", ["estado", "iniciada_en"])
    op.create_table(
        "usuarios",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("google_sub", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("rol", sa.String(32), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "sesiones",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("usuario_id", sa.String(64), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revocada_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sesiones_usuario_id", "sesiones", ["usuario_id"])
    op.create_index("ix_sesiones_expira_en", "sesiones", ["expira_en"])
    for table in ("llamada_turnos", "llamada_decisiones"):
        op.create_table(
            table,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("llamada_id", sa.String(64), sa.ForeignKey("llamadas.id"), nullable=False),
            sa.Column("turno", sa.Integer(), nullable=False),
            sa.Column("datos", postgresql.JSONB(), nullable=False),
            sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            *( [sa.Column("nodo", sa.String(128), nullable=False)] if table == "llamada_decisiones" else [] ),
        )
        op.create_index(f"ix_{table}_llamada_turno", table, ["llamada_id", "turno"])
    op.create_table(
        "pistas_audio",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("llamada_id", sa.String(64), sa.ForeignKey("llamadas.id"), nullable=False),
        sa.Column("canal", sa.String(16), nullable=False),
        sa.Column("clave_objeto", sa.String(512), nullable=False, unique=True),
        sa.Column("mime", sa.String(128), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("duracion_ms", sa.Integer(), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eliminada_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pistas_audio_llamada_id", "pistas_audio", ["llamada_id"])
    op.create_index("ix_pistas_audio_expira_en", "pistas_audio", ["expira_en"])
    op.create_table(
        "auditoria_eventos",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("usuario_id", sa.String(64), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("accion", sa.String(128), nullable=False),
        sa.Column("recurso", sa.String(128), nullable=False),
        sa.Column("detalle", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_auditoria_eventos_usuario_id", "auditoria_eventos", ["usuario_id"])


def downgrade() -> None:
    op.drop_table("auditoria_eventos")
    op.drop_table("pistas_audio")
    op.drop_table("llamada_decisiones")
    op.drop_table("llamada_turnos")
    op.drop_table("sesiones")
    op.drop_table("usuarios")
    op.drop_index("ix_llamadas_estado_iniciada", table_name="llamadas")
    op.drop_column("llamadas", "requiere_revision")
    op.drop_column("llamadas", "finalizada_en")
    op.drop_column("llamadas", "estado")
