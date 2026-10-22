"""Erasure of a call with everything associated with it (spec §5).

Deleted: turns, decisions, audio tracks (and their files), test-bank cases, live state and the
call itself. Its incidents stay as a work record, anonymised: they point to the sentinel call
`deleted-call` and lose description, suggestion and history notes. The audit log keeps that
something was erased, by whom and when, but neither what nor which call: only the fingerprint
of its id, to answer "did you erase my call?" later.

In PostgreSQL it all runs in one transaction with `fincall.erasure = 'on'`, the only mode in
which the append-only audit tables accept those updates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from building_call.audit.install import DELETED_CALL

#: Tables whose rows belong to a single call and disappear with it
CHILD_TABLES = ("call_turns", "call_decisions", "audio_tracks", "bank_cases")


class CallNotErasable(ValueError):
    """The call does not exist, is the sentinel, or is still in progress."""


def fingerprint(value: Any) -> str:
    """Same as `fincall_audit_fingerprint`: first 16 hex of the sha256 of the JSON text. Matches
    PostgreSQL for scalars (a phone, an id); `jsonb` orders object keys its own way."""
    data = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


async def erase_in_postgres(s: AsyncSession, call_id: str) -> list[str]:
    """Erases the call inside the caller's transaction. Returns the audio files to delete."""
    if call_id == DELETED_CALL:
        raise CallNotErasable(call_id)
    state = (
        await s.execute(text("SELECT state FROM calls WHERE id = :id FOR UPDATE"), {"id": call_id})
    ).scalar_one_or_none()
    if state is None:
        raise CallNotErasable(call_id)
    if state == "active":
        raise CallNotErasable(f"{call_id} is in progress")
    await s.execute(text("SELECT set_config('fincall.erasure', 'on', true)"))
    p = {"id": call_id, "deleted": DELETED_CALL}

    children: dict[str, list[str]] = {}
    for table in CHILD_TABLES:
        rows = await s.execute(text(f"SELECT id FROM {table} WHERE call_id = :id"), p)  # noqa: S608
        children[table] = [r[0] for r in rows]
    files = [
        r[0]
        for r in await s.execute(text("SELECT object_key FROM audio_tracks WHERE call_id = :id"), p)
    ]
    incidents = [
        r[0] for r in await s.execute(text("SELECT id FROM incidents WHERE call_id = :id"), p)
    ]

    # 1. The call and its children
    for table in CHILD_TABLES:
        await s.execute(text(f"DELETE FROM {table} WHERE call_id = :id"), p)  # noqa: S608
    await s.execute(text("DELETE FROM call_states WHERE call_id = :id"), p)

    # 2. Its incidents, anonymised
    if incidents:
        q = {"ids": incidents, **p}
        await s.execute(
            text(
                "UPDATE incidents SET call_id = :deleted, description = '', suggestion = NULL "
                "WHERE id = ANY(:ids)"
            ),
            q,
        )
        await s.execute(
            text(
                "UPDATE incident_history SET note = NULL, detail = '{}'::jsonb "
                "WHERE incident_id = ANY(:ids)"
            ),
            q,
        )
    await s.execute(text("DELETE FROM calls WHERE id = :id"), p)

    # 3. The audit log: that something happened, not what
    for table, ids in children.items():
        if ids:
            await _scrub_changes(s, "table_name = :table AND record_id = ANY(:ids)", table, ids)
    await _scrub_changes(s, "table_name = 'calls' AND record_id = :call", "calls", [call_id])
    if incidents:
        # The incidents stay: their rows only lose the reference to the call
        await s.execute(
            text(
                "UPDATE audit_changes SET "
                "before = CASE WHEN before ? 'call_id' "
                "  THEN jsonb_set(before, '{call_id}', to_jsonb(CAST(:deleted AS text))) "
                "  ELSE before END, "
                "after = CASE WHEN after ? 'call_id' "
                "  THEN jsonb_set(after, '{call_id}', to_jsonb(CAST(:deleted AS text))) "
                "  ELSE after END "
                "WHERE table_name = 'incidents' AND record_id = ANY(:ids) "
                "AND (before ->> 'call_id' = :id OR after ->> 'call_id' = :id)"
            ),
            {"ids": incidents, **p},
        )
    viewed = [("calls", [call_id]), *children.items()]
    for resource, ids in viewed:
        if ids:
            await s.execute(
                text(
                    "UPDATE audit_access SET resource_id = :deleted, detail = '{}'::jsonb "
                    "WHERE resource = :resource AND resource_id = ANY(:ids)"
                ),
                {"resource": resource, "ids": ids, **p},
            )
    # The application events from before this log (`audit_events`) are not append-only
    await s.execute(
        text(
            "UPDATE audit_events SET resource = 'call:' || :deleted, detail = '{}'::jsonb "
            "WHERE resource = 'call:' || :id OR strpos(detail::text, :quoted) > 0"
        ),
        {"quoted": json.dumps(call_id), **p},
    )

    # 4. One `erase` row, with the fingerprint of the id
    await s.execute(
        text(
            "INSERT INTO audit_changes (occurred_at, table_name, record_id, operation, actor, "
            "origin, request_id, transaction_id, before, after) VALUES (clock_timestamp(), "
            "'calls', :deleted, 'erase', fincall_audit_actor(), "
            "nullif(current_setting('fincall.origin', true), ''), "
            "nullif(current_setting('fincall.request', true), ''), txid_current(), NULL, "
            "jsonb_build_object('call', fincall_audit_fingerprint(to_jsonb(CAST(:id AS text)))))"
        ),
        p,
    )
    return files


async def _scrub_changes(s: AsyncSession, where: str, table: str, ids: list[str]) -> None:
    await s.execute(
        text(
            "UPDATE audit_changes SET record_id = :deleted, "
            "before = CASE WHEN before IS NULL THEN NULL ELSE '{\"erased\": true}'::jsonb END, "
            "after = CASE WHEN after IS NULL THEN NULL ELSE '{\"erased\": true}'::jsonb END "
            f"WHERE {where}"  # noqa: S608 - fixed fragments, values as parameters
        ),
        {"deleted": DELETED_CALL, "table": table, "ids": ids, "call": ids[0]},
    )


async def delete_files(paths: list[str]) -> int:
    """After the commit: a file that cannot be deleted is logged, the erasure stands."""
    n = 0
    for path in paths:
        try:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
            n += 1
        except OSError:
            continue
    return n
