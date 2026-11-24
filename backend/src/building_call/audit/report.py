"""Audit report, read on request by an operator. Nobody else reads the log (2026-10-22).

    uv run python -m building_call.audit.report changes --table incidents --record inc-1
    uv run python -m building_call.audit.report access --actor user:usr-1 --since 2026-10-01
    uv run python -m building_call.audit.report record calls llam-1
    uv run python -m building_call.audit.report erased llam-1
    uv run python -m building_call.audit.report fingerprint 600111222

Prints JSON. Values of personal fields are never in the log, only their fingerprint: `fingerprint`
gives that of a known value to compare. Reading the log is logged too (`audit_access`, resource
`audit_*`, actor `claude:report`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from building_call.audit import session as audit_session
from building_call.audit.access_log import AccessLog, PostgresAccessStore
from building_call.audit.context import AuditContext, audit_context
from building_call.audit.erasure import fingerprint
from building_call.settings import settings

ACTOR = "claude:report"


def _json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _json(v) for k, v in r.items()} for r in rows]


async def changes(
    sessions: async_sessionmaker[AsyncSession], filters: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    where, params = [], {"limit": limit}
    for column, key in (
        ("table_name", "table"),
        ("record_id", "record"),
        ("actor", "actor"),
        ("operation", "operation"),
    ):
        if filters.get(key) is not None:
            where.append(f"{column} = :{key}")
            params[key] = filters[key]
    if filters.get("since") is not None:
        where.append("occurred_at >= :since")
        params["since"] = filters["since"]
    if filters.get("until") is not None:
        where.append("occurred_at <= :until")
        params["until"] = filters["until"]
    sql = (
        "SELECT id, occurred_at, table_name, record_id, operation, actor, origin, request_id, "
        "transaction_id, before, after FROM audit_changes"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY occurred_at, id LIMIT :limit"
    )
    async with sessions() as s:
        return [dict(r._mapping) for r in await s.execute(text(sql), params)]  # noqa: S608


async def run(args: argparse.Namespace) -> Any:
    if args.command == "fingerprint":
        return {"fingerprint": fingerprint(args.value)}
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    audit_session.install()
    log = AccessLog(PostgresAccessStore(sessions))
    since = datetime.fromisoformat(args.since) if getattr(args, "since", None) else None
    until = datetime.fromisoformat(args.until) if getattr(args, "until", None) else None
    try:
        with audit_context(AuditContext(actor=ACTOR, origin="system")):
            if args.command == "changes":
                filters = {
                    "table": args.table,
                    "record": args.record,
                    "actor": args.actor,
                    "operation": args.operation,
                    "since": since,
                    "until": until,
                }
                rows = _rows(await changes(sessions, filters, args.limit))
                await log.download("audit_changes", None, filter=_rows([filters])[0], rows=len(rows))
                return rows
            if args.command == "access":
                filters = {
                    "resource": args.resource,
                    "resource_id": args.resource_id,
                    "actor": args.actor,
                    "action": args.action,
                    "since": since,
                    "until": until,
                }
                rows = _rows(await log.store.list(filters, args.limit, newest_first=False))
                await log.download("audit_access", None, filter=_rows([filters])[0], rows=len(rows))
                return rows
            if args.command == "record":
                found = {
                    "changes": _rows(
                        await changes(sessions, {"table": args.table, "record": args.id}, 10000)
                    ),
                    "access": _rows(
                        await log.store.list(
                            {"resource": args.table, "resource_id": args.id}, 10000, False
                        )
                    ),
                }
                await log.download("audit_record", args.id, table=args.table)
                return found
            if args.command == "erased":
                # Was this call erased? Only its fingerprint is left in the log
                wanted = fingerprint(args.call_id)
                rows = _rows(
                    await changes(sessions, {"table": "calls", "operation": "erase"}, 100000)
                )
                hits = [r for r in rows if (r["after"] or {}).get("call") == {"fingerprint": wanted}]
                await log.download("audit_erased", None, rows=len(hits))
                return {"fingerprint": wanted, "erased": hits}
            raise ValueError(args.command)
    finally:
        await engine.dispose()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="building_call.audit.report", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("changes", help="Inserts, updates, deletes and erasures")
    c.add_argument("--table")
    c.add_argument("--record")
    c.add_argument("--actor")
    c.add_argument("--operation", choices=["insert", "update", "delete", "erase"])
    a = sub.add_parser("access", help="Reads, lists, views, plays and downloads")
    a.add_argument("--resource")
    a.add_argument("--resource-id")
    a.add_argument("--actor")
    a.add_argument("--action", choices=["read", "list", "view", "play", "download"])
    for q in (c, a):
        q.add_argument("--since", help="ISO date or datetime")
        q.add_argument("--until", help="ISO date or datetime")
        q.add_argument("--limit", type=int, default=500)
    r = sub.add_parser("record", help="Everything about one record: its changes and who saw it")
    r.add_argument("table")
    r.add_argument("id")
    e = sub.add_parser("erased", help="Whether a call was erased, by whom and when")
    e.add_argument("call_id")
    f = sub.add_parser("fingerprint", help="Fingerprint of a known value, to compare")
    f.add_argument("value")
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    result = asyncio.run(run(args))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
