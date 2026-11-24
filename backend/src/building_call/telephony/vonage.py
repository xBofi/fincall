"""Vonage Voice API adapter: answer webhook (NCCO) + bidirectional WebSocket audio.

NOT TESTED LIVE: there is no Vonage account behind this project yet. Everything here is built
from Vonage's official docs (read 2026-11-24) and covered by tests with fake frames only:

- https://developer.vonage.com/en/voice/voice-api/concepts/websockets
- https://developer.vonage.com/en/voice/voice-api/ncco-reference
- https://developer.vonage.com/en/voice/voice-api/webhook-reference
- https://developer.vonage.com/en/getting-started/concepts/webhooks (signed webhooks)

Flow: Vonage receives the call → answer webhook `GET {PUBLIC_URL}/api/telephony/vonage/answer`
with `to`, `from`, `uuid`, `conversation_uuid` (signed: `Authorization: Bearer <JWT HS256>` with
the account's signature secret) → we return an NCCO with one `connect` action whose endpoint is
`{"type": "websocket", "uri": "wss://.../media", "content-type": "audio/l16;rate=16000",
"headers": {...}}` → Vonage opens the WebSocket and sends a text message
`{"event": "websocket:connected", "content-type": ..., <our headers as top-level keys>}`, then
binary frames of raw 16-bit little-endian PCM (~20 ms each). We send audio back as binary frames
in the same format, `{"action": "notify", "payload": {...}}` as a playback marker (Vonage answers
`{"event": "websocket:notify", "payload": {...}}` once the queued audio before it has played),
and `{"action": "clear"}` for barge-in. Key presses arrive as
`{"event": "websocket:dtmf", "digit": "5", "duration": 260}`. Closing the socket with code 1000
ends the `connect` leg; with no more NCCO actions, the call ends.

Twilio vs Vonage, side by side: `docs/TELEFONIA.md`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from building_call import i18n
from building_call.telephony.bridge import CallBridge
from building_call.telephony.carrier import LINEAR16_16K

logger = logging.getLogger(__name__)

ANSWER_PATH = "/api/telephony/vonage/answer"
EVENT_PATH = "/api/telephony/vonage/event"
MEDIA_PATH = "/api/telephony/vonage/media"
HEALTH_PATH = "/api/telephony/vonage/health"

CONTENT_TYPE = f"audio/l16;rate={LINEAR16_16K.rate}"
#: A signed webhook older than this is refused (replay protection on top of the signature)
MAX_WEBHOOK_AGE_S = 300


def verify_signed_webhook(
    signature_secret: str,
    authorization: str | None,
    body: bytes = b"",
    *,
    now: float | None = None,
) -> bool:
    """Vonage signed webhook: `Authorization: Bearer <JWT>` signed HS256 with the signature
    secret, `iss == "Vonage"`, and `payload_hash == sha256(body)` when there is a body."""
    if not signature_secret or not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(
            token,
            signature_secret,
            algorithms=["HS256"],
            options={"require": ["iat"], "verify_iat": False},
        )
    except jwt.PyJWTError:
        return False
    if claims.get("iss") != "Vonage":
        return False
    current = time.time() if now is None else now
    if abs(current - float(claims["iat"])) > MAX_WEBHOOK_AGE_S:
        return False
    if body:
        expected = hashlib.sha256(body).hexdigest()
        if not hmac.compare_digest(str(claims.get("payload_hash", "")), expected):
            return False
    return True


def stream_token(secret: str, call_uuid: str) -> str:
    """Per-call token we put in the NCCO headers. It comes back in `websocket:connected`, so
    only a WebSocket opened by Vonage for a call our signed answer webhook accepted gets in."""
    return hmac.new(secret.encode(), f"vonage-ws:{call_uuid}".encode(), hashlib.sha256).hexdigest()


def ws_url_from_public(public_url: str) -> str:
    base = public_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + MEDIA_PATH


def ncco_connect_websocket(
    ws_url: str, event_url: str, headers: dict[str, str], from_number: str | None = None
) -> list[dict[str, Any]]:
    connect: dict[str, Any] = {
        "action": "connect",
        "eventUrl": [event_url],
        "endpoint": [
            {
                "type": "websocket",
                "uri": ws_url,
                "content-type": CONTENT_TYPE,
                "headers": headers,
            }
        ],
    }
    if from_number:
        connect["from"] = from_number
    return [connect]


def call_language(params: dict[str, str]) -> str:
    """The call's language. Vonage's answer webhook does not carry one, so it comes from the
    Answer URL configured for the number (`.../answer?language=en`); otherwise the default.
    An unknown code falls back to the default instead of failing the call."""
    return i18n.normalize_code(params.get("language")) or i18n.default_code()


def ncco_unavailable(language: str | None = None) -> list[dict[str, Any]]:
    """Spoken when telephony is not ready (NCCO `talk`; with no more actions the call ends)."""
    spec = i18n.get(language)
    return [{"action": "talk", "text": spec.phrases.service_unavailable, "language": spec.locale}]


class VonageChannel:
    """`CarrierChannel` over a Vonage WebSocket: linear16 16 kHz in binary frames."""

    audio_format = LINEAR16_16K

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.hung_up = False

    async def send_audio(self, frame: bytes) -> None:
        await self.ws.send_bytes(frame)

    async def mark(self, name: str) -> None:
        await self.ws.send_text(json.dumps({"action": "notify", "payload": {"mark": name}}))

    async def clear(self) -> None:
        await self.ws.send_text(json.dumps({"action": "clear"}))

    async def hang_up(self) -> None:
        # Code 1000 is the intentional close: no reconnection, and with no NCCO actions left
        # after the `connect`, Vonage ends the call.
        if self.hung_up:
            return
        self.hung_up = True
        try:
            await self.ws.close(code=1000)
        except Exception:  # noqa: BLE001
            pass


#: (channel, caller phone, call already opened or None, language or None)
BridgeFactory = Callable[[VonageChannel, str | None, str | None, str | None], Awaitable[CallBridge]]


def not_ready_reason(settings: Any, tts_available: bool, stt_available: bool) -> str | None:
    """Why Vonage calls cannot be answered yet (no secrets in the message)."""
    if "vonage" not in providers(settings):
        return "BC_TELEPHONY no incluye 'vonage'"
    if not settings.public_url.startswith("https://"):
        return "BC_PUBLIC_URL debe ser una URL https pública (túnel o dominio)"
    if not settings.vonage_signature_secret:
        return "Falta BC_VONAGE_SIGNATURE_SECRET (firma de los webhooks de Vonage)"
    if not stt_available:
        return "Falta BC_ASSEMBLYAI_API_KEY (STT en streaming)"
    if not tts_available:
        return "No hay TTS de servidor"
    return None


def providers(settings: Any) -> set[str]:
    return {p.strip() for p in str(settings.telephony).split(",") if p.strip()}


def create_vonage_router(
    settings: Any, bridge_factory: BridgeFactory, ready: Callable[[], str | None]
) -> APIRouter:
    r = APIRouter()

    @r.get(HEALTH_PATH)
    async def health() -> dict[str, Any]:
        reason = ready()
        return {"ok": reason is None, "reason": reason}

    async def _answer(
        request: Request, params: dict[str, str], body: bytes
    ) -> list[dict[str, Any]]:
        if not verify_signed_webhook(
            settings.vonage_signature_secret, request.headers.get("Authorization"), body
        ):
            logger.warning("Webhook de Vonage con firma inválida desde %s", request.client)
            raise HTTPException(403, "Firma de Vonage no válida")
        reason = ready()
        if reason is not None:
            logger.error("Llamada de Vonage con la telefonía sin preparar: %s", reason)
            return ncco_unavailable(call_language(params))
        call_uuid = params.get("uuid", "")
        if not call_uuid:
            raise HTTPException(400, "Falta uuid")
        headers = {
            "call_uuid": call_uuid,
            "phone": params.get("from", ""),
            "token": stream_token(settings.vonage_signature_secret, call_uuid),
            "language": call_language(params),
        }
        return ncco_connect_websocket(
            ws_url_from_public(settings.public_url),
            settings.public_url.rstrip("/") + EVENT_PATH,
            headers,
        )

    # Answer webhooks are GET by default; POST (JSON) if the application sets answer_method
    @r.get(ANSWER_PATH)
    async def answer_get(request: Request) -> list[dict[str, Any]]:
        return await _answer(request, dict(request.query_params), b"")

    @r.post(ANSWER_PATH)
    async def answer_post(request: Request) -> list[dict[str, Any]]:
        body = await request.body()
        try:
            params = {k: str(v) for k, v in json.loads(body or b"{}").items()}
        except (ValueError, AttributeError) as e:
            raise HTTPException(400, "Cuerpo no válido") from e
        return await _answer(request, params, body)

    @r.post(EVENT_PATH)
    async def event(request: Request) -> dict[str, Any]:
        body = await request.body()
        if not verify_signed_webhook(
            settings.vonage_signature_secret, request.headers.get("Authorization"), body
        ):
            raise HTTPException(403, "Firma de Vonage no válida")
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        logger.info(
            "Vonage %s: %s%s",
            data.get("uuid"),
            data.get("status"),
            f" ({data.get('cause')}, {data.get('close_code')})" if data.get("cause") else "",
        )
        return {"ok": True}

    @r.websocket(MEDIA_PATH)
    async def media(ws: WebSocket) -> None:
        await ws.accept()
        bridge: CallBridge | None = None
        try:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if (frame := message.get("bytes")) is not None:
                    if bridge is not None:
                        await bridge.receive_audio(frame)
                    continue
                try:
                    m = json.loads(message.get("text") or "{}")
                except ValueError:
                    continue
                event_name = m.get("event")
                if event_name == "websocket:connected" and bridge is None:
                    call_uuid = str(m.get("call_uuid", ""))
                    expected = stream_token(settings.vonage_signature_secret, call_uuid)
                    if (
                        ready() is not None
                        or not call_uuid
                        or m.get("content-type") != CONTENT_TYPE
                        or not hmac.compare_digest(str(m.get("token", "")), expected)
                    ):
                        logger.warning("WebSocket de Vonage rechazado: sin token válido")
                        await ws.close(code=1008)
                        return
                    bridge = await bridge_factory(
                        VonageChannel(ws),
                        m.get("phone") or None,
                        None,
                        i18n.normalize_code(m.get("language")),
                    )
                    await bridge.begin()
                elif event_name == "websocket:notify" and bridge is not None:
                    await bridge.mark_played(str((m.get("payload") or {}).get("mark", "")))
                elif event_name == "websocket:dtmf" and bridge is not None:
                    await bridge.receive_dtmf(str(m.get("digit", "")))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Fallo en el WebSocket de Vonage")
        finally:
            if bridge is not None:
                await bridge.close()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    return r
