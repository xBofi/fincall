"""Índices para las consultas que hace la consola al crecer el histórico.

- Historial: últimas llamadas (`ORDER BY iniciada_en DESC`) y llamadas de una finca.
- Auditoría: últimos eventos.
- Configuración: la más reciente de un ámbito (`ambito, ambito_id, valido_desde DESC`).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_llamadas_iniciada_en", "llamadas", ["iniciada_en"])
    op.create_index("ix_llamadas_finca_id", "llamadas", ["finca_id"])
    op.create_index("ix_auditoria_eventos_ocurrido_en", "auditoria_eventos", ["ocurrido_en"])
    op.create_index(
        "ix_configuraciones_ambito_vigente",
        "configuraciones",
        ["ambito", "ambito_id", "valido_desde"],
    )


def downgrade() -> None:
    op.drop_index("ix_configuraciones_ambito_vigente", table_name="configuraciones")
    op.drop_index("ix_auditoria_eventos_ocurrido_en", table_name="auditoria_eventos")
    op.drop_index("ix_llamadas_finca_id", table_name="llamadas")
    op.drop_index("ix_llamadas_iniciada_en", table_name="llamadas")
