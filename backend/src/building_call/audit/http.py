"""ASGI middleware that opens the audit context of every HTTP and WebSocket request.

It sets the request id (the `X-Request-ID` header if present, otherwise a new one) and the
channel. Authentication sets the actor afterwards (`Access`, the agent secret); the Twilio
routes have no session and are recorded as the telephony. Pure ASGI rather than
`BaseHTTPMiddleware`: the route runs in the same context, with no copies between tasks.
No IP or browser is recorded (2026-10-22): only who.
"""

from __future__ import annotations

from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send

from building_call.audit.context import AuditContext, audit_context

TELEPHONY_PREFIX = "/api/telephony/"


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        telephony = scope["path"].startswith(TELEPHONY_PREFIX)
        context = AuditContext(
            actor="telephony:twilio" if telephony else "anonymous",
            request_id=(headers.get("x-request-id") or uuid4().hex)[:64],
            origin="telephony" if telephony else "console",
        )
        with audit_context(context):
            await self.app(scope, receive, send)
