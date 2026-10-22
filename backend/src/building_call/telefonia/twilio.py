"""Adaptador Twilio: webhook de voz (TwiML) + Media Streams bidireccional por WebSocket.

Flujo: Twilio recibe la llamada → `POST {URL_PUBLICA}/api/telefonia/twilio/voz` (firmado con
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

from building_call.telefonia.puente import PuenteLlamada

logger = logging.getLogger(__name__)

RUTA_VOZ = "/api/telefonia/twilio/voz"
RUTA_MEDIA = "/api/telefonia/twilio/media"
RUTA_ESTADO_LLAMADA = "/api/telefonia/twilio/estado-llamada"
RUTA_SALUD = "/api/telefonia/twilio/salud"


def firma_valida(auth_token: str, url: str, parametros: dict[str, str], firma: str | None) -> bool:
    """Firma Twilio: base64(HMAC-SHA1(token, url + claves ordenadas concatenadas con sus valores))."""
    if not firma or not auth_token:
        return False
    cadena = url + "".join(k + parametros[k] for k in sorted(parametros))
    esperada = base64.b64encode(
        hmac.new(auth_token.encode(), cadena.encode("utf-8"), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(esperada, firma)


def token_stream(auth_token: str, call_sid: str) -> str:
    """Token del Media Stream de una llamada. Lo pone nuestro webhook (firmado por Twilio) en
    el TwiML y vuelve en el evento `start`: sin él, cualquiera podía abrir el WebSocket y hablar
    con el bot (revisión de seguridad 2026-10-22)."""
    return hmac.new(
        auth_token.encode(), f"media-stream:{call_sid}".encode(), hashlib.sha256
    ).hexdigest()


def twiml_conectar_stream(url_ws: str, parametros: dict[str, str]) -> str:
    params = "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in parametros.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(url_ws)}>{params}</Stream></Connect></Response>"
    )


def twiml_decir_y_colgar(texto: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say language="es-ES">{escape(texto)}</Say><Hangup/></Response>'
    )


def url_ws_desde_publica(url_publica: str) -> str:
    base = url_publica.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + RUTA_MEDIA


class CanalTwilio:
    """Implementa `CanalTelefono` sobre el WebSocket de Media Streams."""

    def __init__(self, ws: WebSocket, stream_sid: str) -> None:
        self.ws = ws
        self.stream_sid = stream_sid
        self.colgado = False

    async def enviar_audio(self, ulaw: bytes) -> None:
        await self.ws.send_json(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": base64.b64encode(ulaw).decode()},
            }
        )

    async def marcar(self, nombre: str) -> None:
        await self.ws.send_json(
            {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": nombre}}
        )

    async def limpiar(self) -> None:
        await self.ws.send_json({"event": "clear", "streamSid": self.stream_sid})

    async def colgar(self) -> None:
        if self.colgado:
            return
        self.colgado = True
        try:
            await self.ws.close(code=1000)
        except Exception:  # noqa: BLE001
            pass


FabricaPuente = Callable[[CanalTwilio, str | None], Awaitable[PuenteLlamada]]


def crear_router_twilio(
    settings: Any, fabrica_puente: FabricaPuente, listo: Callable[[], str | None]
) -> APIRouter:
    """`listo()` devuelve None si todo está configurado o el motivo por el que no (sin secretos)."""
    r = APIRouter()

    @r.get(RUTA_SALUD)
    async def salud() -> dict[str, Any]:
        motivo = listo()
        return {"ok": motivo is None, "motivo": motivo}

    @r.post(RUTA_VOZ)
    async def voz(request: Request) -> Response:
        formulario = {k: str(v) for k, v in (await request.form()).items()}
        url = settings.url_publica.rstrip("/") + RUTA_VOZ
        if not firma_valida(
            settings.twilio_auth_token, url, formulario, request.headers.get("X-Twilio-Signature")
        ):
            logger.warning("Webhook de Twilio con firma inválida desde %s", request.client)
            raise HTTPException(403, "Firma de Twilio no válida")
        motivo = listo()
        if motivo is not None:
            logger.error("Llamada entrante con la telefonía sin preparar: %s", motivo)
            return Response(
                twiml_decir_y_colgar(
                    "Lo sentimos, el servicio no está disponible en este momento. Inténtelo más tarde."
                ),
                media_type="application/xml",
            )
        call_sid = formulario.get("CallSid", "")
        parametros = {
            "telefono": formulario.get("From", ""),
            "call_sid": call_sid,
            "token": token_stream(settings.twilio_auth_token, call_sid),
        }
        return Response(
            twiml_conectar_stream(url_ws_desde_publica(settings.url_publica), parametros),
            media_type="application/xml",
        )

    @r.post(RUTA_ESTADO_LLAMADA)
    async def estado_llamada(request: Request) -> dict[str, Any]:
        formulario = {k: str(v) for k, v in (await request.form()).items()}
        url = settings.url_publica.rstrip("/") + RUTA_ESTADO_LLAMADA
        if not firma_valida(
            settings.twilio_auth_token, url, formulario, request.headers.get("X-Twilio-Signature")
        ):
            raise HTTPException(403, "Firma de Twilio no válida")
        logger.info(
            "Twilio %s: %s (%s s)",
            formulario.get("CallSid"),
            formulario.get("CallStatus"),
            formulario.get("CallDuration", "-"),
        )
        return {"ok": True}

    @r.websocket(RUTA_MEDIA)
    async def media(ws: WebSocket) -> None:
        await ws.accept()
        puente: PuenteLlamada | None = None
        try:
            while True:
                m = await ws.receive_json()
                evento = m.get("event")
                if evento == "start":
                    inicio = m.get("start") or {}
                    stream_sid = str(m.get("streamSid") or inicio.get("streamSid") or "")
                    parametros = inicio.get("customParameters") or {}
                    esperado = token_stream(
                        settings.twilio_auth_token, str(parametros.get("call_sid", ""))
                    )
                    if (
                        listo() is not None
                        or not settings.twilio_auth_token
                        or not parametros.get("call_sid")
                        or not hmac.compare_digest(str(parametros.get("token", "")), esperado)
                    ):
                        logger.warning("Media Stream rechazado: sin token válido de su llamada")
                        await ws.close(code=1008)
                        return
                    canal = CanalTwilio(ws, stream_sid)
                    puente = await fabrica_puente(canal, parametros.get("telefono") or None)
                    await puente.iniciar()
                elif evento == "media" and puente is not None:
                    medio = m.get("media") or {}
                    if medio.get("track", "inbound") == "inbound":
                        await puente.recibir_ulaw(base64.b64decode(medio.get("payload", "")))
                elif evento == "mark" and puente is not None:
                    await puente.marca_reproducida(str((m.get("mark") or {}).get("name", "")))
                elif evento == "stop":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Fallo en el Media Stream de Twilio")
        finally:
            if puente is not None:
                await puente.cerrar()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    return r
