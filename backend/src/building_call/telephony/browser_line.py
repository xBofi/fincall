"""A phone line in the browser, without Twilio: the console's microphone through the phone path.

The console opens a WebSocket that speaks the Twilio Media Streams protocol (JSON events, μ-law
8 kHz in base64, `media` / `mark` / `clear` / `stop`) and the server plugs it into the same
`CallBridge` as a real call: same STT session, server TTS converted to μ-law in 20 ms frames,
barge-in, delivery ledger and recording of both voices. Only the carrier is missing, so it
needs no Twilio account, no number and no public URL, and costs nothing.

Use it to hear the agent exactly as a phone caller would, in any language, before a number
exists. The Twilio browser call (`telephony.browser`) is the same test over the real network.

Protocol (both ways, as in https://www.twilio.com/docs/voice/media-streams/websocket-messages):
- client -> server: {"event": "start", "start": {"customParameters": {"call_id": "..."}}}, then
  {"event": "media", "media": {"payload": "<base64 μ-law>"}}, {"event": "mark", "mark":
  {"name": "..."}} once a mark has played, {"event": "stop"}.
- server -> client: {"event": "media", ...}, {"event": "mark", ...}, {"event": "clear"}.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from building_call.telephony.bridge import CallBridge
from building_call.telephony.carrier import MULAW_8K

logger = logging.getLogger(__name__)

LINE_PATH = "/api/telephony/browser/line"
#: A frame bigger than 1 s of μ-law is not a phone frame (Twilio sends 20 ms)
MAX_FRAME_BYTES = 8000


class BrowserLineChannel:
    """`CarrierChannel` over the console's WebSocket: the same events Twilio would get."""

    audio_format = MULAW_8K

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.hung_up = False

    async def send_audio(self, ulaw: bytes) -> None:
        await self.ws.send_json({"event": "media", "media": {"payload": base64.b64encode(ulaw).decode()}})

    async def mark(self, name: str) -> None:
        await self.ws.send_json({"event": "mark", "mark": {"name": name}})

    async def clear(self) -> None:
        await self.ws.send_json({"event": "clear"})

    async def hang_up(self) -> None:
        if self.hung_up:
            return
        self.hung_up = True
        try:
            await self.ws.close(code=1000)
        except Exception:  # noqa: BLE001 - already closed by the browser
            pass


#: (channel, call already opened by the console) -> bridge
LineBridgeFactory = Callable[[BrowserLineChannel, str], Awaitable[CallBridge]]


def create_browser_line_router(
    authorize: Callable[[WebSocket], Awaitable[bool]],
    bridge_factory: LineBridgeFactory,
    ready: Callable[[], str | None],
    call_exists: Callable[[str], Awaitable[bool]],
) -> APIRouter:
    """`authorize`: the console session (cookie) of the WebSocket. `ready`: None when STT and
    server TTS are there, or why not. `call_exists`: the call the console opened, still open."""
    r = APIRouter()

    @r.websocket(LINE_PATH)
    async def line(ws: WebSocket) -> None:
        await ws.accept()
        if not await authorize(ws):
            await ws.close(code=1008)
            return
        bridge: CallBridge | None = None
        try:
            while True:
                m: dict[str, Any] = await ws.receive_json()
                event = m.get("event")
                if event == "start" and bridge is None:
                    call_id = str(((m.get("start") or {}).get("customParameters") or {}).get("call_id", ""))
                    reason = ready()
                    if reason is not None or not call_id or not await call_exists(call_id):
                        await ws.send_json({"event": "error", "message": reason or "Llamada no encontrada"})
                        await ws.close(code=1008)
                        return
                    bridge = await bridge_factory(BrowserLineChannel(ws), call_id)
                    await bridge.begin()
                elif event == "media" and bridge is not None:
                    payload = base64.b64decode((m.get("media") or {}).get("payload", ""))
                    if len(payload) <= MAX_FRAME_BYTES:
                        await bridge.receive_audio(payload)
                elif event == "mark" and bridge is not None:
                    await bridge.mark_played(str((m.get("mark") or {}).get("name", "")))
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Fallo en la línea del navegador")
        finally:
            if bridge is not None:
                await bridge.close()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    return r
