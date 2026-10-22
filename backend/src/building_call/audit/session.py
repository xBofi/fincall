"""Copies the audit context to PostgreSQL when every transaction starts.

`set_config(..., true)` lasts for the transaction only: a pooled connection never carries the
actor of the previous request. The triggers read it with `current_setting('fincall.actor')`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction

from building_call.audit.context import current_context

_SET = text(
    "SELECT set_config('fincall.actor', :actor, true), "
    "set_config('fincall.origin', :origin, true), "
    "set_config('fincall.request', :request, true)"
)


def parameters() -> dict[str, Any]:
    ctx = current_context()
    return {"actor": ctx.actor, "origin": ctx.origin, "request": ctx.request_id or ""}


def _on_begin(_session: Session, _transaction: SessionTransaction, connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(_SET, parameters())


def install() -> None:
    """Idempotent: every SQLAlchemy session (async ones included) goes through it."""
    if not event.contains(Session, "after_begin", _on_begin):
        event.listen(Session, "after_begin", _on_begin)
