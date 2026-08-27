"""Esquema inicial: administradores, especialistas, fincas, viviendas, configuraciones,
llamadas, incidencias e historial.

Revision ID: 0001
Revises:
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "administradores",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("nombre", sa.String(200), nullable=False),
    )
    op.create_table(
        "especialistas",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "administrador_id", sa.String(64), sa.ForeignKey("administradores.id"), nullable=False
        ),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column("categoria_principal", sa.String(64), nullable=False),
        sa.Column("canal_contacto", sa.String(16), nullable=False),
        sa.Column("destino", sa.String(200), nullable=False),
    )
    op.create_index("ix_especialistas_administrador_id", "especialistas", ["administrador_id"])
    op.create_table(
        "fincas",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "administrador_id", sa.String(64), sa.ForeignKey("administradores.id"), nullable=False
        ),
        sa.Column("direccion", sa.String(300), nullable=False),
        sa.Column("correo", sa.String(200), nullable=False),
        sa.Column("telefono_oficial", sa.String(32), nullable=False),
    )
    op.create_index("ix_fincas_administrador_id", "fincas", ["administrador_id"])
    op.create_table(
        "viviendas",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("finca_id", sa.String(64), sa.ForeignKey("fincas.id"), nullable=False),
        sa.Column("portal", sa.String(16), nullable=True),
        sa.Column("piso", sa.String(16), nullable=False),
        sa.Column("puerta", sa.String(16), nullable=False),
    )
    op.create_index("ix_viviendas_finca_id", "viviendas", ["finca_id"])
    op.create_table(
        "configuraciones",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("ambito", sa.String(16), nullable=False),
        sa.Column("ambito_id", sa.String(64), nullable=False),
        sa.Column("documento", postgresql.JSONB(), nullable=False),
        sa.Column(
            "valido_desde", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_configuraciones_ambito_id", "configuraciones", ["ambito_id"])
    op.create_table(
        "llamadas",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telefono_llamante", sa.String(32), nullable=True),
        sa.Column("finca_id", sa.String(64), sa.ForeignKey("fincas.id"), nullable=True),
        sa.Column("vivienda_id", sa.String(64), sa.ForeignKey("viviendas.id"), nullable=True),
        sa.Column("nombre_declarado", sa.String(200), nullable=True),
        sa.Column("transcripcion", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("resumen", sa.Text(), nullable=True),
        sa.Column("resultado", sa.String(40), nullable=True),
        sa.Column("motivo_revision", sa.Text(), nullable=True),
        sa.Column("configuracion_id", sa.String(64), nullable=True),
        sa.Column("grafo", sa.String(64), nullable=True),
    )
    op.create_table(
        "incidencias",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("llamada_id", sa.String(64), sa.ForeignKey("llamadas.id"), nullable=False),
        sa.Column("finca_id", sa.String(64), sa.ForeignKey("fincas.id"), nullable=False),
        sa.Column("vivienda_id", sa.String(64), sa.ForeignKey("viviendas.id"), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("categoria", sa.String(64), nullable=False),
        sa.Column(
            "especialista_id", sa.String(64), sa.ForeignKey("especialistas.id"), nullable=True
        ),
        sa.Column("urgencia", sa.String(16), nullable=False),
        sa.Column("estado", sa.String(32), nullable=False, server_default="pendiente"),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incidencias_llamada_id", "incidencias", ["llamada_id"])
    op.create_index("ix_incidencias_finca_id", "incidencias", ["finca_id"])
    op.create_table(
        "incidencia_historial",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("incidencia_id", sa.String(64), sa.ForeignKey("incidencias.id"), nullable=False),
        sa.Column("estado_anterior", sa.String(32), nullable=True),
        sa.Column("estado_nuevo", sa.String(32), nullable=False),
        sa.Column(
            "cambiado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("nota", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_incidencia_historial_incidencia_id", "incidencia_historial", ["incidencia_id"]
    )


def downgrade() -> None:
    for tabla in (
        "incidencia_historial",
        "incidencias",
        "llamadas",
        "configuraciones",
        "viviendas",
        "fincas",
        "especialistas",
        "administradores",
    ):
        op.drop_table(tabla)
