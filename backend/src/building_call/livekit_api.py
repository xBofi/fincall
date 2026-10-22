"""LiveKit: token de sala para la consola y acceso del agente de voz a la API.

El agente (`building_call.voz.agente`) es otro proceso: usa las mismas rutas de llamada que la
consola, autenticado con `BC_AGENTE_SECRETO` en vez de con una sesión de usuario. La API sigue
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

from building_call.acceso import Acceso
from building_call.settings import settings

#: Identidad del agente en la API (quién hizo la petición). El nombre con el que se registra en
#: LiveKit y que piden las salas es `settings.livekit_agente`
NOMBRE_AGENTE = "fincall"


#: Un secreto corto ("agente", "secret") da acceso sin sesión a todas las llamadas
SECRETO_MINIMO = 32


def es_agente(request: Request | WebSocket) -> bool:
    secreto = settings.agente_secreto
    cabecera = request.headers.get("authorization", "")
    if len(secreto) < SECRETO_MINIMO or not cabecera.startswith("Bearer "):
        return False
    return hmac.compare_digest(cabecera.removeprefix("Bearer ").encode(), secreto.encode())


def requiere_usuario_o_agente(acceso: Acceso) -> Callable[..., Coroutine[Any, Any, Any]]:
    usuario = acceso.requiere()

    async def dependencia(request: Request, response: Response) -> Any:
        if es_agente(request):
            return NOMBRE_AGENTE
        return await usuario(request, response)

    return dependencia


class PeticionToken(BaseModel):
    llamada_id: str


def livekit_configurado() -> bool:
    return bool(settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret)


def crear_router_livekit(acceso: Acceso) -> APIRouter:
    router = APIRouter()

    usuario_consola = Depends(acceso.requiere())

    @router.post("/api/livekit/token")
    async def token(cuerpo: PeticionToken, usuario: Any = usuario_consola) -> dict[str, str]:
        """Sala nueva para probar una llamada ya iniciada; el agente se une a ella por despacho."""
        if not livekit_configurado():
            raise HTTPException(503, "LiveKit no está configurado")
        if settings.livekit_url_consola and len(settings.livekit_api_secret) < SECRETO_MINIMO:
            # Las claves de `livekit-server --dev` (devkey/secret) son públicas: con la consola
            # expuesta fuera de localhost cualquiera podría firmar tokens (seguridad 2026-10-22)
            raise HTTPException(503, "LiveKit con claves de desarrollo: solo en localhost")
        from livekit import api

        sala = f"consola-{uuid4().hex[:12]}"
        despacho = api.RoomAgentDispatch(
            agent_name=settings.livekit_agente,
            metadata=json.dumps({"llamada_id": cuerpo.llamada_id}),
        )
        jwt = (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(f"consola-{usuario.nombre_usuario}-{uuid4().hex[:6]}")
            .with_name(usuario.nombre or usuario.nombre_usuario)
            .with_grants(api.VideoGrants(room_join=True, room=sala))
            .with_room_config(api.RoomConfiguration(agents=[despacho]))
            .to_jwt()
        )
        return {
            "url": settings.livekit_url_consola or settings.livekit_url,
            "token": jwt,
            "sala": sala,
        }

    return router
