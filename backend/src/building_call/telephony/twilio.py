"""Adaptador Twilio: webhook de voz (TwiML) + Media Streams bidireccional por WebSocket.

Flujo: Twilio recibe la llamada → `POST {URL_PUBLICA}/api/telephony/twilio/voice` (firmado con
`X-Twilio-Signature`) → respondemos TwiML `<Connect><Stream url="wss://.../media">` → Twilio abre el
WebSocket y envía μ-law 8 kHz en base64 (`media`); nosotros devolvemos audio del bot (`media`),
`mark` para saber cuándo terminó de sonar y `clear` para cortar (barge-in). Cerrar el WebSocket
termina el `<Connect>` y, al no haber más TwiML, la llamada.

Referencia: https://www.twilio.com/docs/voice/media-streams/websocket-messages
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from building_call.telephony.bridge import CallBridge

logger = logging.getLogger(__name__)

VOICE_PATH = "/api/telephony/twilio/voice"
MEDIA_PATH = "/api/telephony/twilio/media"
CALL_STATUS_PATH = "/api/telephony/twilio/estado-llamada"
HEALTH_PATH = "/api/telephony/twilio/health"


def valid_signature(auth_token: str, url: str, parameters: dict[str, str], signature: str | None) -> bool:
    """Firma Twilio: base64(HMAC-SHA1(token, url + claves ordenadas concatenadas con sus valores))."""
    if not signature or not auth_token:
        return False
    base_string = url + "".join(k + parameters[k] for k in sorted(parameters))
    expected_sig = base64.b64encode(
        hmac.new(auth_token.encode(), base_string.encode("utf-8"), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(expected_sig, signature)


def token_stream(auth_token: str, call_sid: str) -> str:
    """Token del Media Stream de una llamada. Lo pone nuestro webhook (firmado por Twilio) en
    el TwiML y vuelve en el evento `start`: sin él, cualquiera podía abrir el WebSocket y hablar
    con el bot (revisión de seguridad 2026-10-22)."""
    return hmac.new(
        auth_token.encode(), f"media-stream:{call_sid}".encode(), hashlib.sha256
    ).hexdigest()


def twiml_connect_stream(url_ws: str, parameters: dict[str, str]) -> str:
    params = "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in parameters.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(url_ws)}>{params}</Stream></Connect></Response>"
    )


def twiml_say_and_hang_up(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say language="es-ES">{escape(text)}</Say><Hangup/></Response>'
    )


def ws_url_from_public(public_url: str) -> str:
    base = public_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + MEDIA_PATH


class TwilioChannel:
    """Implementa `PhoneChannel` sobre el WebSocket de Media Streams."""

    def __init__(self, ws: WebSocket, stream_sid: str) -> None:
        self.ws = ws
        self.stream_sid = stream_sid
        self.hung_up = False

    async def send_audio(self, ulaw: bytes) -> None:
        await self.ws.send_json(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": base64.b64encode(ulaw).decode()},
            }
        )

    async def mark(self, name: str) -> None:
        await self.ws.send_json(
            {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}}
        )

    async def clean_up(self) -> None:
        await self.ws.send_json({"event": "clear", "streamSid": self.stream_sid})

    async def hang_up(self) -> None:
        if self.hung_up:
            return
        self.hung_up = True
        try:
            await self.ws.close(code=1000)
        except Exception:  # noqa: BLE001
            pass


BridgeFactory = Callable[[TwilioChannel, str | None], Awaitable[CallBridge]]


def create_twilio_router(
    settings: Any, bridge_factory: BridgeFactory, ready: Callable[[], str | None]
) -> APIRouter:
    """`listo()` devuelve None si todo está configurado o el motivo por el que no (sin secretos)."""
    r = APIRouter()

    @r.get(HEALTH_PATH)
    async def health() -> dict[str, Any]:
        reason = ready()
        return {"ok": reason is None, "reason": reason}

    @r.post(VOICE_PATH)
    async def voice(request: Request) -> Response:
        form_data = {k: str(v) for k, v in (await request.form()).items()}
        url = settings.public_url.rstrip("/") + VOICE_PATH
        if not valid_signature(
            settings.twilio_auth_token, url, form_data, request.headers.get("X-Twilio-Signature")
        ):
            logger.warning("Webhook de Twilio con firma inválida desde %s", request.client)
            raise HTTPException(403, "Firma de Twilio no válida")
        reason = ready()
        if reason is not None:
            logger.error("Llamada entrante con la telefonía sin preparar: %s", reason)
            return Response(
                twiml_say_and_hang_up(
                    "Lo sentimos, el servicio no está disponible en este momento. Inténtelo más tarde."
                ),
                media_type="application/xml",
            )
        call_sid = form_data.get("CallSid", "")
        parameters = {
            "phone": form_data.get("From", ""),
            "call_sid": call_sid,
            "token": token_stream(settings.twilio_auth_token, call_sid),
        }
        return Response(
            twiml_connect_stream(ws_url_from_public(settings.public_url), parameters),
            media_type="application/xml",
        )

    @r.post(CALL_STATUS_PATH)
    async def call_state(request: Request) -> dict[str, Any]:
        form_data = {k: str(v) for k, v in (await request.form()).items()}
        url = settings.public_url.rstrip("/") + CALL_STATUS_PATH
        if not valid_signature(
            settings.twilio_auth_token, url, form_data, request.headers.get("X-Twilio-Signature")
        ):
            raise HTTPException(403, "Firma de Twilio no válida")
        logger.info(
            "Twilio %s: %s (%s s)",
            form_data.get("CallSid"),
            form_data.get("CallStatus"),
            form_data.get("CallDuration", "-"),
        )
        return {"ok": True}

    @r.websocket(MEDIA_PATH)
    async def medium(ws: WebSocket) -> None:
        await ws.accept()
        bridge: CallBridge | None = None
        try:
            while True:
                m = await ws.receive_json()
                event = m.get("event")
                if event == "start":
                    start = m.get("start") or {}
                    stream_sid = str(m.get("streamSid") or start.get("streamSid") or "")
                    parameters = start.get("customParameters") or {}
                    expected = token_stream(
                        settings.twilio_auth_token, str(parameters.get("call_sid", ""))
                    )
                    if (
                        ready() is not None
                        or not settings.twilio_auth_token
                        or not parameters.get("call_sid")
                        or not hmac.compare_digest(str(parameters.get("token", "")), expected)
                    ):
                        logger.warning("Media Stream rechazado: sin token válido de su llamada")
                        await ws.close(code=1008)
                        return
                    channel = TwilioChannel(ws, stream_sid)
                    bridge = await bridge_factory(channel, parameters.get("phone") or None)
                    await bridge.begin()
                elif event == "media" and bridge is not None:
                    midpoint = m.get("media") or {}
                    if midpoint.get("track", "inbound") == "inbound":
                        await bridge.receive_ulaw(base64.b64decode(midpoint.get("payload", "")))
                elif event == "mark" and bridge is not None:
                    await bridge.mark_played(str((m.get("mark") or {}).get("name", "")))
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Fallo en el Media Stream de Twilio")
        finally:
            if bridge is not None:
                await bridge.close()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    return r
