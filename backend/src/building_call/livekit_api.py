"""LiveKit: token de sala para la consola y acceso del agente de voz a la API.

El agente (`building_call.voice.agent`) es otro proceso: usa las mismas rutas de llamada que la
consola, autenticado con `BC_AGENT_SECRET` en vez de con una sesión de usuario. La API sigue
siendo la única dueña del estado de la llamada.
Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md`.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel

from building_call.access import Access
from building_call.audit.context import set_actor
from building_call.settings import settings

#: Identidad del agente en la API (quién hizo la petición). El nombre con el que se registra en
#: LiveKit y que piden las salas es `settings.livekit_agent`
AGENT_NAME = "fincall"


#: Un secreto corto ("agente", "secret") da acceso sin sesión a todas las llamadas
MIN_SECRET_CHARS = 32


def is_agent(request: Request | WebSocket) -> bool:
    secret = settings.agent_secret
    header = request.headers.get("authorization", "")
    if len(secret) < MIN_SECRET_CHARS or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header.removeprefix("Bearer ").encode(), secret.encode())


def requires_user_or_agent(access: Access) -> Callable[..., Coroutine[Any, Any, Any]]:
    user = access.requires()

    async def dependency(request: Request, response: Response) -> Any:
        if is_agent(request):
            set_actor("agent:livekit", "agent")
            return AGENT_NAME
        return await user(request, response)

    return dependency


class TokenRequest(BaseModel):
    call_id: str


def livekit_configured() -> bool:
    return bool(settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret)


def create_livekit_router(access: Access) -> APIRouter:
    router = APIRouter()

    console_user = Depends(access.requires())

    @router.post("/api/livekit/token")
    async def token(body: TokenRequest, user: Any = console_user) -> dict[str, str]:
        """Sala nueva para probar una llamada ya iniciada; el agente se une a ella por despacho."""
        if not livekit_configured():
            raise HTTPException(503, "LiveKit no está configurado")
        if settings.livekit_console_url and len(settings.livekit_api_secret) < MIN_SECRET_CHARS:
            # Las claves de `livekit-server --dev` (devkey/secret) son públicas: con la consola
            # expuesta fuera de localhost cualquiera podría firmar tokens (seguridad 2026-10-22)
            raise HTTPException(503, "LiveKit con claves de desarrollo: solo en localhost")
        from livekit import api

        room = f"consola-{uuid4().hex[:12]}"
        dispatch = api.RoomAgentDispatch(
            agent_name=settings.livekit_agent,
            metadata=json.dumps({"call_id": body.call_id}),
        )
        jwt = (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(f"consola-{user.username}-{uuid4().hex[:6]}")
            .with_name(user.name or user.username)
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .with_room_config(api.RoomConfiguration(agents=[dispatch]))
            .to_jwt()
        )
        return {
            "url": settings.livekit_console_url or settings.livekit_url,
            "token": jwt,
            "room": room,
        }

    return router
