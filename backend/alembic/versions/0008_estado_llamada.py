"""Estado vivo de la llamada persistido: una llamada sobrevive a un reinicio de la API.

Hasta ahora `GestorLlamadas` guardaba el estado de cada llamada en curso en un diccionario del
proceso: al reiniciar la API la llamada se perdía y no podía haber dos instancias. Una fila por
llamada con el estado del grafo en JSONB, el turno, una versión que sube en cada escritura y la
hora de la última, que gobierna la retención (15 min tras terminar, 2 h si se abandona).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "estados_llamada",
        sa.Column("llamada_id", sa.String(64), primary_key=True),
        sa.Column("estado", postgresql.JSONB(), nullable=False),
        sa.Column("turno", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("finalizada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_estados_llamada_actualizada_en", "estados_llamada", ["actualizada_en"])


def downgrade() -> None:
    op.drop_index("ix_estados_llamada_actualizada_en", table_name="estados_llamada")
    op.drop_table("estados_llamada")
