"""Who is acting in this request, for the audit trail.

A middleware opens the context with the request id and the channel; authentication sets the
actor. Every database transaction copies it to PostgreSQL (`audit/session.py`), where the
triggers read it. Being a `ContextVar`, two concurrent requests never mix their actor, and
tasks started from a request inherit it. Only *who* is recorded: no IP, no browser.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

#: Written without a context (a script, a task with no request): shown as such, never as a user
NO_CONTEXT_ACTOR = "system:no_context"


@dataclass(frozen=True)
class AuditContext:
    actor: str = NO_CONTEXT_ACTOR
    request_id: str | None = None
    #: console | agent | telephony | system
    origin: str = "system"


_context: ContextVar[AuditContext] = ContextVar("audit_context")


def current_context() -> AuditContext:
    return _context.get(AuditContext())


@contextmanager
def audit_context(context: AuditContext) -> Iterator[AuditContext]:
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def set_actor(actor: str, origin: str | None = None) -> None:
    """Who it is, once authenticated. Keeps the request id."""
    current = current_context()
    _context.set(replace(current, actor=actor, origin=origin or current.origin))
