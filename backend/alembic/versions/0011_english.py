"""The schema in English: tables, columns, indexes, constraints and the codes stored in them.

The product stays in Spanish: free text (what the caller said, what the bot said, addresses,
descriptions, notes, template texts) is never translated. What is translated:

- table, column, index and constraint names;
- columns that hold codes (`state = 'activa'` -> `'active'`, categories, urgencies, roles,
  node and template ids, audit actions);
- the keys of stored JSON documents, and the values in them that are codes;
- the placeholders inside template texts (`{direccion}` -> `{address}`).

The live state of calls in progress (`call_states`) is dropped instead of translated: it only
lives while a call is active and the console recreates it.

The names and codes come from `0011_english.json`, a snapshot frozen with this migration, so
later code changes never alter what it does. The downgrade renames the schema back; it does
not translate data back (several Spanish codes map to the same English one).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-22
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SNAPSHOT = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
TABLES: list[dict[str, Any]] = SNAPSHOT["tables"]
RENAMES: dict[str, str] = SNAPSHOT["renames"]

#: Columns that hold a code, by new table name
CODE_COLUMNS = {
    "calls": ["state", "result", "graph", "review_reason"],
    "users": ["role", "access_state"],
    "specialists": ["main_category", "contact_channel"],
    "incidents": ["category", "urgency", "state", "work_type"],
    "incident_history": ["previous_state", "new_state", "action"],
    "call_decisions": ["node"],
    "audit_events": ["action", "resource"],
    "bank_cases": ["bank", "node"],
    "audio_tracks": ["channel"],
    "configs": ["scope"],
}
#: JSON columns, by new table name, with their primary key
JSON_COLUMNS = {
    "calls": ("id", ["transcript"]),
    "call_turns": ("id", ["data"]),
    "call_decisions": ("id", ["data"]),
    "configs": ("id", ["document"]),
    "incident_history": ("id", ["detail"]),
    "audit_events": ("id", ["detail"]),
    "bank_cases": ("id", ["expected", "equipment_list"]),
}
#: Keys whose values are free text in Spanish: never translated
TEXT_KEYS = {
    "caller",
    "assistant",
    "text",
    "detail",
    "objection",
    "problem",
    "address",
    "name",
    "question",
    "summary",
    "description",
    "note",
    "suggestion",
    "email",
    "official_phone",
    "reply",
    "utterance",
    "heard_text",
    "request",
    "short_problem",
    "declared_name",
    "username",
    "location",
    "kind",
    "brand",
    "model",
    "installer",
    "contract",
    "target",
}

_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def code(value: str) -> str:
    """A stored code in English: `activa`, `identificar_finca.candidata`, `llamada:ll-1`,
    `actuacion:siniestro`. Anything else (free text, ids) is left as it is."""
    if _IDENT.fullmatch(value):
        return RENAMES.get(value, value)
    if re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", value):
        return ".".join(RENAMES.get(p, p) for p in value.split("."))
    prefix = re.fullmatch(r"([a-z_]+):(.*)", value, re.S)
    if prefix and prefix.group(1) in RENAMES:
        rest = prefix.group(2)
        return RENAMES[prefix.group(1)] + ":" + (code(rest) if _IDENT.fullmatch(rest) else rest)
    return value


def document(value: Any, parent: str | None = None) -> Any:
    """A stored JSON document: keys always, values only when they are codes."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = RENAMES.get(k, k)
            out[key] = document(v, key)
        return out
    if isinstance(value, list):
        return [document(v, parent) for v in value]
    if isinstance(value, str) and parent not in TEXT_KEYS:
        return code(value)
    return value


def placeholders(text: str) -> str:
    return re.sub(r"\{([a-z_]+)\}", lambda m: "{" + RENAMES.get(m.group(1), m.group(1)) + "}", text)


def _rename(name: str, old_table: str, new_table: str, columns: dict[str, str]) -> str:
    if name in RENAMES:
        return RENAMES[name]
    new = name.replace(old_table, new_table, 1)
    for old, fresh in sorted(columns.items(), key=lambda kv: -len(kv[0])):
        new = re.sub(rf"(?<=_){re.escape(old)}(?=_|$)", fresh, new)
    return new[:63]


def _rename_indexes_and_constraints(
    old_table: str, new_table: str, columns: dict[str, str]
) -> None:
    conn = op.get_bind()
    constraints = (
        conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = :t"
            ),
            {"t": new_table},
        )
        .scalars()
        .all()
    )
    for name in constraints:
        fresh = _rename(name, old_table, new_table, columns)
        if fresh != name:
            op.execute(f'ALTER TABLE "{new_table}" RENAME CONSTRAINT "{name}" TO "{fresh}"')
    # Indexes not backing a constraint (those were renamed with it)
    indexes = (
        conn.execute(
            sa.text(
                "SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
                "JOIN pg_class t ON t.oid = x.indrelid WHERE t.relname = :t "
                "AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid)"
            ),
            {"t": new_table},
        )
        .scalars()
        .all()
    )
    for name in indexes:
        fresh = _rename(name, old_table, new_table, columns)
        if fresh != name:
            op.execute(f'ALTER INDEX "{name}" RENAME TO "{fresh}"')


def _translate_data() -> None:
    conn = op.get_bind()
    for table, columns in CODE_COLUMNS.items():
        for column in columns:
            values = (
                conn.execute(
                    sa.text(
                        f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
                    )
                )
                .scalars()
                .all()
            )
            for value in values:
                fresh = code(value)
                if fresh != value:
                    conn.execute(
                        sa.text(f'UPDATE "{table}" SET "{column}" = :new WHERE "{column}" = :old'),
                        {"new": fresh, "old": value},
                    )
    for table, (pk, columns) in JSON_COLUMNS.items():
        for column in columns:
            rows = conn.execute(
                sa.text(f'SELECT "{pk}", "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL')
            ).all()
            for key, value in rows:
                fresh = document(value)
                if fresh != value:
                    conn.execute(
                        sa.text(
                            f'UPDATE "{table}" SET "{column}" = CAST(:v AS JSONB) WHERE "{pk}" = :k'
                        ),
                        {"v": json.dumps(fresh, ensure_ascii=False), "k": key},
                    )
    # Template overrides: their id and the placeholders in their text
    for table, id_column in (("templates", "id"), ("template_versions", "template_id")):
        rows = conn.execute(sa.text(f'SELECT "{id_column}", text FROM "{table}"')).all()
        for template_id, text in rows:
            conn.execute(
                sa.text(
                    f'UPDATE "{table}" SET "{id_column}" = :new_id, text = :text '
                    f'WHERE "{id_column}" = :old_id AND text = :old_text'
                ),
                {
                    "new_id": code(template_id),
                    "text": placeholders(text),
                    "old_id": template_id,
                    "old_text": text,
                },
            )
    # A call in progress cannot survive a change of every key in its state
    conn.execute(sa.text('DELETE FROM "call_states"'))


def upgrade() -> None:
    for t in TABLES:
        if t["old"] != t["new"]:
            op.rename_table(t["old"], t["new"])
        for old, new in t["columns"].items():
            op.alter_column(t["new"], old, new_column_name=new)
    for t in TABLES:
        _rename_indexes_and_constraints(t["old"], t["new"], t["columns"])
    _translate_data()


def downgrade() -> None:
    inverse_names = {new: old for old, new in RENAMES.items()}
    for t in TABLES:
        inverse = {new: old for old, new in t["columns"].items()}
        _rename_back(t["new"], t["old"], inverse, inverse_names)
        for new, old in inverse.items():
            op.alter_column(t["new"], new, new_column_name=old)
        if t["old"] != t["new"]:
            op.rename_table(t["new"], t["old"])


def _rename_back(
    table: str, old_table: str, columns: dict[str, str], inverse_names: dict[str, str]
) -> None:
    """Indexes and constraints back to their Spanish names, which older downgrades use."""
    conn = op.get_bind()

    def back(name: str) -> str:
        if name in inverse_names:
            return inverse_names[name]
        old = name.replace(table, old_table, 1)
        for new, previous in sorted(columns.items(), key=lambda kv: -len(kv[0])):
            old = re.sub(rf"(?<=_){re.escape(new)}(?=_|$)", previous, old)
        return old[:63]

    for name in (
        conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = :t"
            ),
            {"t": table},
        )
        .scalars()
        .all()
    ):
        if back(name) != name:
            op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{name}" TO "{back(name)}"')
    for name in (
        conn.execute(
            sa.text(
                "SELECT i.relname FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
                "JOIN pg_class t ON t.oid = x.indrelid WHERE t.relname = :t "
                "AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = x.indexrelid)"
            ),
            {"t": table},
        )
        .scalars()
        .all()
    ):
        if back(name) != name:
            op.execute(f'ALTER INDEX "{name}" RENAME TO "{back(name)}"')
