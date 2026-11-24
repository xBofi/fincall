"""Audit trail: who changed what, and who looked at what.

- `audit_created_at`, `audit_created_by`, `audit_updated_at`, `audit_updated_by` on every
  business table, written by a trigger (the application cannot forge them).
- `audit_changes`: one row per insert, update and delete, written by triggers.
- `audit_access`: who read, listed, viewed, played or downloaded what, and for how long.
- Both append-only. No IP or browser: only who (2026-10-22).
- The sentinel call `deleted-call`, that the incidents of an erased call point to.
- The caller's name is no longer kept: `calls.declared_name` goes, and so does the name in the
  stored interpretations, the live state and the decision log.

The triggers are generated from each class's static attributes (`audit/install.py`). A later
migration that changes those attributes calls `install_sql()` again.
Spec: `docs/superpowers/specs/2026-10-22-audit-trail-design.md`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from building_call.audit.install import DELETED_CALL, install_sql, uninstall_sql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

#: Business tables at this revision
TABLES = (
    "administrators",
    "audio_tracks",
    "bank_cases",
    "call_decisions",
    "call_states",
    "call_turns",
    "calls",
    "configs",
    "equipment",
    "estates",
    "homes",
    "incident_history",
    "incidents",
    "sessions",
    "specialists",
    "template_versions",
    "templates",
    "users",
)


def _moment(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, _moment("audit_created_at"))
        # Null on the rows from before the audit: nobody knows who created them
        op.add_column(table, sa.Column("audit_created_by", sa.String(160), nullable=True))
        op.add_column(table, _moment("audit_updated_at"))
        op.add_column(table, sa.Column("audit_updated_by", sa.String(160), nullable=True))

    op.create_table(
        "audit_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("table_name", sa.String(64), nullable=False),
        sa.Column("record_id", sa.String(200), nullable=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("origin", sa.String(32), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_audit_changes_occurred_at", "audit_changes", ["occurred_at"])
    op.create_index(
        "ix_audit_changes_record", "audit_changes", ["table_name", "record_id", "occurred_at"]
    )
    op.create_index("ix_audit_changes_actor", "audit_changes", ["actor", "occurred_at"])

    op.create_table(
        "audit_access",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("origin", sa.String(32), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_audit_access_started_at", "audit_access", ["started_at"])
    op.create_index(
        "ix_audit_access_resource", "audit_access", ["resource", "resource_id", "started_at"]
    )
    op.create_index("ix_audit_access_actor", "audit_access", ["actor", "started_at"])

    # The caller's name, out of everything stored (before the triggers: not a change to log)
    op.drop_column("calls", "declared_name")
    op.execute(
        "UPDATE call_turns SET data = jsonb_set(data, '{interpretation}', "
        "(data -> 'interpretation') - 'name') "
        "WHERE jsonb_typeof(data -> 'interpretation') = 'object' AND data -> 'interpretation' ? 'name'"
    )
    op.execute(
        "DELETE FROM call_decisions WHERE data ->> 'action' IN ('name_registered')"
        " OR (data ->> 'action' = 'correction' AND data ->> 'detail' LIKE 'nombre -> %')"
    )
    op.execute("DELETE FROM call_states")
    op.execute("DELETE FROM templates WHERE id = 'collect_request.greeting_with_name'")
    op.execute(
        "DELETE FROM template_versions WHERE template_id = 'collect_request.greeting_with_name'"
    )

    op.execute(
        sa.text(
            "INSERT INTO calls (id, started_at, transcript, state, needs_review, result) "
            "VALUES (:id, '1970-01-01T00:00:00Z', '[]'::jsonb, 'finished', false, NULL)"
        ).bindparams(id=DELETED_CALL)
    )

    for statement in install_sql():
        op.execute(statement)


def downgrade() -> None:
    for statement in uninstall_sql():
        op.execute(statement)
    op.drop_table("audit_access")
    op.drop_table("audit_changes")
    # The incidents of erased calls keep pointing to the sentinel: it only goes if none does
    op.execute(
        sa.text(
            "DELETE FROM calls WHERE id = :id "
            "AND NOT EXISTS (SELECT 1 FROM incidents WHERE call_id = :id)"
        ).bindparams(id=DELETED_CALL)
    )
    op.add_column("calls", sa.Column("declared_name", sa.String(200), nullable=True))
    for table in TABLES:
        for column in ("audit_updated_by", "audit_updated_at", "audit_created_by", "audit_created_at"):
            op.drop_column(table, column)
