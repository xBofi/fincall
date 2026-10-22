"""Audit API: only the views, for anyone in the console.

The console opens a view when a record with personal data is on screen, sends a heartbeat
while it stays and closes it when it leaves. Nobody can query the log from the console
(Andreu, 2026-10-22): Andreu asks Claude, who reads it with `building_call.audit.report`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from building_call.access import Access
from building_call.audit.access_log import HEARTBEAT_S, NotAuditable
from building_call.domain.operations import User


class ViewOpening(BaseModel):
    resource: str
    resource_id: str


def create_audit_router(access: Access) -> APIRouter:
    r = APIRouter(prefix="/api/audit")
    person = access.requires()
    log = access.access_log

    @r.post("/views", status_code=201)
    async def open_view(body: ViewOpening, _: User = Depends(person)) -> dict[str, Any]:  # noqa: B008
        try:
            view_id = await log.open_view(body.resource, body.resource_id)
        except NotAuditable as e:
            raise HTTPException(422, f"No se registran vistas de «{e}»") from e
        return {"id": view_id, "heartbeat_s": HEARTBEAT_S}

    @r.post("/views/{view_id}/heartbeat", status_code=204)
    async def heartbeat(view_id: str, _: User = Depends(person)) -> Response:  # noqa: B008
        if not await log.heartbeat(view_id):
            raise HTTPException(404, "Vista no abierta")
        return Response(status_code=204)

    @r.post("/views/{view_id}/end", status_code=204)
    async def end(view_id: str, _: User = Depends(person)) -> Response:  # noqa: B008
        # Idempotent: `sendBeacon` may repeat it when the page closes
        await log.close_view(view_id)
        return Response(status_code=204)

    return r
