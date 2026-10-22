"""Access log: who read, listed, viewed, played or downloaded what, and when.

- `read` / `list`: the API served a record or a list (an instant).
- `view`: the record was on screen. The console opens the view, sends a heartbeat every
  `HEARTBEAT_S` and closes it on unmount, hidden tab or page close. A view with no heartbeat for
  `ABANDON_S` ends at its last heartbeat.
- `play` / `download`: a file left the application.

Only records of tables with personal fields are logged: the classes' static attributes are the
single source. If the log cannot be written the request fails: no personal data leaves without
a trace.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import and_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from building_call.audit.context import current_context
from building_call.audit.install import DELETED_CALL
from building_call.repos.sql_models import AuditAccessSQL, AuditedTable

ACTIONS = ("read", "list", "view", "play", "download")
HEARTBEAT_S = 30
ABANDON_S = 120

Row = dict[str, Any]


class NotAuditable(ValueError):
    """The table does not exist or has no personal fields: there is no view to log."""


def personal_data_tables() -> dict[str, type[AuditedTable]]:
    from building_call.audit.install import audited_tables

    return {c.__tablename__: c for c in audited_tables() if c.has_personal_data()}


class AccessStore(Protocol):
    async def add(self, row: Row) -> None: ...
    async def heartbeat(self, view_id: str, actor: str, moment: datetime) -> bool: ...
    async def close(self, view_id: str, actor: str, moment: datetime, reason: str) -> bool: ...
    async def close_abandoned(self, limit: datetime) -> int: ...
    async def erase(self, call_id: str) -> None: ...
    async def list(
        self, filters: dict[str, Any], limit: int = 500, newest_first: bool = True
    ) -> list[Row]: ...


def _matches(row: Row, filters: dict[str, Any]) -> bool:
    for key in ("resource", "resource_id", "actor", "action"):
        if filters.get(key) is not None and row[key] != filters[key]:
            return False
    if filters.get("since") is not None and row["started_at"] < filters["since"]:
        return False
    return not (filters.get("until") is not None and row["started_at"] > filters["until"])


class MemoryAccessStore:
    """Development without a database (`BC_REPOSITORY=memory`)."""

    def __init__(self) -> None:
        self.rows: list[Row] = []

    async def add(self, row: Row) -> None:
        self.rows.append(dict(row))

    def _open(self, view_id: str, actor: str) -> Row | None:
        return next(
            (
                r
                for r in self.rows
                if r["id"] == view_id
                and r["actor"] == actor
                and r["action"] == "view"
                and r["ended_at"] is None
            ),
            None,
        )

    async def heartbeat(self, view_id: str, actor: str, moment: datetime) -> bool:
        row = self._open(view_id, actor)
        if row is None:
            return False
        row["last_heartbeat_at"] = moment
        return True

    async def close(self, view_id: str, actor: str, moment: datetime, reason: str) -> bool:
        row = self._open(view_id, actor)
        if row is None:
            return False
        row["ended_at"] = moment
        row["detail"] = {**row["detail"], "close": reason}
        return True

    async def close_abandoned(self, limit: datetime) -> int:
        n = 0
        for r in self.rows:
            last = r["last_heartbeat_at"] or r["started_at"]
            if r["action"] == "view" and r["ended_at"] is None and last < limit:
                r["ended_at"] = last
                r["detail"] = {**r["detail"], "close": "no_heartbeat"}
                n += 1
        return n

    async def erase(self, call_id: str) -> None:
        for r in self.rows:
            if r["resource_id"] == call_id or r["detail"].get("call_id") == call_id:
                r["resource_id"], r["detail"] = DELETED_CALL, {}

    async def list(
        self, filters: dict[str, Any], limit: int = 500, newest_first: bool = True
    ) -> list[Row]:
        rows = [dict(r) for r in self.rows if _matches(r, filters)]
        if newest_first:
            rows.reverse()
        return rows[:limit]


class PostgresAccessStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def add(self, row: Row) -> None:
        async with self.sessions() as s, s.begin():
            s.add(AuditAccessSQL(**row))

    def _open(self, view_id: str, actor: str) -> Any:
        c = AuditAccessSQL
        return and_(c.id == view_id, c.actor == actor, c.action == "view", c.ended_at.is_(None))

    async def heartbeat(self, view_id: str, actor: str, moment: datetime) -> bool:
        async with self.sessions() as s, s.begin():
            r = await s.execute(
                update(AuditAccessSQL)
                .where(self._open(view_id, actor))
                .values(last_heartbeat_at=moment)
            )
            return bool(r.rowcount)  # type: ignore[attr-defined]

    async def close(self, view_id: str, actor: str, moment: datetime, reason: str) -> bool:
        async with self.sessions() as s, s.begin():
            r = await s.execute(
                text(
                    "UPDATE audit_access SET ended_at = :moment, "
                    "detail = detail || jsonb_build_object('close', CAST(:reason AS text)) "
                    "WHERE id = :id AND actor = :actor AND action = 'view' AND ended_at IS NULL"
                ),
                {"moment": moment, "reason": reason, "id": view_id, "actor": actor},
            )
            return bool(r.rowcount)  # type: ignore[attr-defined]

    async def close_abandoned(self, limit: datetime) -> int:
        async with self.sessions() as s, s.begin():
            r = await s.execute(
                text(
                    "UPDATE audit_access "
                    "SET ended_at = coalesce(last_heartbeat_at, started_at), "
                    "detail = detail || '{\"close\": \"no_heartbeat\"}'::jsonb "
                    "WHERE action = 'view' AND ended_at IS NULL "
                    "AND coalesce(last_heartbeat_at, started_at) < :limit"
                ),
                {"limit": limit},
            )
            return int(r.rowcount)  # type: ignore[attr-defined]

    async def erase(self, call_id: str) -> None:
        """Nothing to do: the erasure scrubs `audit_access` in its own transaction."""

    async def list(
        self, filters: dict[str, Any], limit: int = 500, newest_first: bool = True
    ) -> list[Row]:
        c = AuditAccessSQL
        q = select(c)
        for key in ("resource", "resource_id", "actor", "action"):
            if filters.get(key) is not None:
                q = q.where(getattr(c, key) == filters[key])
        if filters.get("since") is not None:
            q = q.where(c.started_at >= filters["since"])
        if filters.get("until") is not None:
            q = q.where(c.started_at <= filters["until"])
        order = (c.started_at.desc(), c.id.desc()) if newest_first else (c.started_at, c.id)
        async with self.sessions() as s:
            rows = (await s.execute(q.order_by(*order).limit(limit))).scalars()
            return [{k: getattr(r, k) for k in c.__table__.c.keys()} for r in rows]


class AccessLog:
    def __init__(
        self, store: AccessStore, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.store = store
        self.clock = clock

    async def _add(
        self,
        action: str,
        resource: str,
        resource_id: str | None,
        detail: dict[str, Any],
        open_view: bool = False,
    ) -> str:
        ctx = current_context()
        now = self.clock()
        row = {
            "id": f"acc-{uuid4().hex}",
            "action": action,
            "resource": resource,
            "resource_id": resource_id,
            "actor": ctx.actor,
            "origin": ctx.origin,
            "request_id": ctx.request_id,
            "started_at": now,
            "ended_at": None if open_view else now,
            "last_heartbeat_at": None,
            "detail": detail,
        }
        await self.store.add(row)
        return str(row["id"])

    async def read(self, model: type[AuditedTable], resource_id: str, **detail: Any) -> None:
        if model.has_personal_data():
            await self._add("read", model.__tablename__, resource_id, detail)

    async def list(self, model: type[AuditedTable], filters: dict[str, Any], rows: int) -> None:
        if model.has_personal_data():
            used = {k: v for k, v in filters.items() if v is not None}
            await self._add("list", model.__tablename__, None, {"filter": used, "rows": rows})

    async def play(self, model: type[AuditedTable], resource_id: str, **detail: Any) -> None:
        await self._add("play", model.__tablename__, resource_id, detail)

    async def download(self, resource: str, resource_id: str | None, **detail: Any) -> None:
        await self._add("download", resource, resource_id, detail)

    # ----------------------------------------------------------------------------- views

    async def open_view(self, resource: str, resource_id: str) -> str:
        if resource not in personal_data_tables():
            raise NotAuditable(resource)
        return await self._add("view", resource, resource_id, {}, open_view=True)

    async def heartbeat(self, view_id: str) -> bool:
        return await self.store.heartbeat(view_id, current_context().actor, self.clock())

    async def close_view(self, view_id: str) -> bool:
        return await self.store.close(view_id, current_context().actor, self.clock(), "closed")

    async def close_abandoned(self) -> int:
        return await self.store.close_abandoned(self.clock() - timedelta(seconds=ABANDON_S))

    async def erase(self, call_id: str) -> None:
        """An erased call: its rows keep that someone looked, not at which call."""
        await self.store.erase(call_id)
