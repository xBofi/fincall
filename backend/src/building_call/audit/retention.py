"""Purge of the audit log after its retention period (two years, Confirmed by Andreu).

It is the only thing that may delete from the log: it marks the transaction with
`fincall.purge = 'on'`, which the append-only trigger accepts for `DELETE` and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def purge_audit(
    sessions: async_sessionmaker[AsyncSession], days: int, now: datetime
) -> dict[str, int]:
    limit = now - timedelta(days=days)
    async with sessions() as s, s.begin():
        await s.execute(text("SELECT set_config('fincall.purge', 'on', true)"))
        changes = await s.execute(
            text("DELETE FROM audit_changes WHERE occurred_at < :limit"), {"limit": limit}
        )
        accesses = await s.execute(
            text("DELETE FROM audit_access WHERE started_at < :limit"), {"limit": limit}
        )
    return {"changes": int(changes.rowcount), "accesses": int(accesses.rowcount)}  # type: ignore[attr-defined]
