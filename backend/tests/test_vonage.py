"""Vonage adapter: signed webhooks, NCCO, and a whole call over the WebSocket with fake frames.

There is no Vonage account behind these tests: the message shapes come from Vonage's docs
(see the module docstring of `telephony/vonage.py`). A live call has never been made.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import time
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_telephony import FakeStt, FakeTts, _wait

from building_call import i18n
from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.services import CallManager
from building_call.settings import settings as global_settings
from building_call.stt_streaming import TurnEvent
from building_call.telephony import audio
from building_call.telephony.bridge import CallBridge
from building_call.telephony.carrier import LINEAR16_16K, MULAW_8K, CarrierChannel
from building_call.telephony.twilio import TwilioChannel
from building_call.telephony.vonage import (
    ANSWER_PATH,
    CONTENT_TYPE,
    EVENT_PATH,
    MEDIA_PATH,
    VonageChannel,
    ncco_connect_websocket,
    not_ready_reason,
    stream_token,
    verify_signed_webhook,
)

SECRET = "vonage-signature-secret-for-tests"


def _signed(
    body: bytes = b"", *, secret: str = SECRET, iat: float | None = None, **claims: Any
) -> str:
    payload: dict[str, Any] = {
        "iat": int(time.time() if iat is None else iat),
        "jti": "j1",
        "iss": "Vonage",
        "api_key": "abc123",
        **claims,
    }
    if body:
        payload["payload_hash"] = hashlib.sha256(body).hexdigest()
    return "Bearer " + jwt.encode(payload, secret, algorithm="HS256")


def _tone16k(ms: int) -> bytes:
    n = 16 * ms
    return struct.pack(
        f"<{n}h", *(int(8000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n))
    )


# ---------------------------------------------------------------- interface


def test_both_carriers_implement_the_same_channel() -> None:
    assert isinstance(VonageChannel(ws=None), CarrierChannel)  # type: ignore[arg-type]
    assert isinstance(TwilioChannel(ws=None, stream_sid="MZ1"), CarrierChannel)  # type: ignore[arg-type]
    assert VonageChannel.audio_format == LINEAR16_16K and TwilioChannel.audio_format == MULAW_8K
    assert LINEAR16_16K.frame_bytes == 640 and MULAW_8K.frame_bytes == 160


def test_outbound_linear16_frames_are_20ms_and_padded_with_zero() -> None:
    out = audio.OutboundAudio(16000, LINEAR16_16K)
    chunks = out.convert(_tone16k(50), final=True)
    assert [len(c) for c in chunks] == [640, 640, 640]
    assert chunks[-1].endswith(b"\x00\x00")
    # Inbound linear16 16 kHz is already what the STT wants: no conversion
    pcm = _tone16k(20)
    assert audio.InboundAudio(LINEAR16_16K).convert(pcm) == pcm


# ---------------------------------------------------------------- signed webhooks


def test_signed_webhook_checks_secret_issuer_age_and_body_hash() -> None:
    body = b'{"status":"answered"}'
    assert verify_signed_webhook(SECRET, _signed(body), body)
    assert verify_signed_webhook(SECRET, _signed(), b"")  # GET answer webhook: no body
    assert not verify_signed_webhook(SECRET, _signed(body, secret="other"), body)
    assert not verify_signed_webhook(SECRET, _signed(body), b'{"status":"tampered"}')
    assert not verify_signed_webhook(SECRET, _signed(iss="NotVonage"), b"")
    assert not verify_signed_webhook(SECRET, _signed(iat=time.time() - 3600), b"")
    assert not verify_signed_webhook(SECRET, None, b"")
    assert not verify_signed_webhook("", _signed(), b"")
    assert not verify_signed_webhook(SECRET, "Basic abc", b"")


def test_ncco_connects_a_linear16_websocket_with_our_headers() -> None:
    ncco = ncco_connect_websocket(
        "wss://bot.example/media", "https://bot.example/event", {"call_uuid": "u1"}
    )
    assert ncco == [
        {
            "action": "connect",
            "eventUrl": ["https://bot.example/event"],
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": "wss://bot.example/media",
                    "content-type": "audio/l16;rate=16000",
                    "headers": {"call_uuid": "u1"},
                }
            ],
        }
    ]


def test_not_ready_reason_for_vonage() -> None:
    from types import SimpleNamespace

    s = SimpleNamespace(telephony="twilio", public_url="https://x", vonage_signature_secret="")
    assert "vonage" in str(not_ready_reason(s, True, True))
    s.telephony = "twilio, vonage"
    assert "SIGNATURE" in str(not_ready_reason(s, True, True))
    s.vonage_signature_secret = "s"
    assert not_ready_reason(s, True, True) is None
    assert "TTS" in str(not_ready_reason(s, False, True))


# ---------------------------------------------------------------- routes


@pytest.fixture
def vonage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_settings, "telephony", "vonage")
    monkeypatch.setattr(global_settings, "public_url", "https://bot.example")
    monkeypatch.setattr(global_settings, "vonage_signature_secret", SECRET)
    monkeypatch.setattr(global_settings, "tts", "none")


def test_answer_webhook_refuses_bad_signature_and_apologises_if_not_ready(
    deps: Dependencies, vonage_env: None
) -> None:
    app = create_app(deps, streaming_factory=lambda *_a: FakeStt())
    with TestClient(app) as c:
        params = {"to": "+34911", "from": "+34600", "uuid": "u1", "conversation_uuid": "CON-1"}
        assert c.get(ANSWER_PATH, params=params).status_code == 403
        r = c.get(ANSWER_PATH, params=params, headers={"Authorization": _signed()})
        assert r.status_code == 200
        assert r.json()[0]["action"] == "talk"  # no TTS: not ready, apologise
        assert c.get("/api/telephony/vonage/health").json()["ok"] is False


def test_answer_webhook_returns_the_ncco_when_ready(
    deps: Dependencies, vonage_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("building_call.api.main.vonage_not_ready", lambda *_a, **_k: None)
    app = create_app(deps, streaming_factory=lambda *_a: FakeStt())
    with TestClient(app) as c:
        params = {"to": "+34911", "from": "+34600", "uuid": "u1", "conversation_uuid": "CON-1"}
        ncco = c.get(ANSWER_PATH, params=params, headers={"Authorization": _signed()}).json()
        endpoint = ncco[0]["endpoint"][0]
        assert endpoint["uri"] == "wss://bot.example" + MEDIA_PATH
        assert endpoint["content-type"] == CONTENT_TYPE
        assert endpoint["headers"] == {
            "call_uuid": "u1",
            "phone": "+34600",
            "token": stream_token(SECRET, "u1"),
            "language": "es",
        }
        assert ncco[0]["eventUrl"] == ["https://bot.example" + EVENT_PATH]
        # POST variant (answer_method=POST): JSON body, signed with its hash
        body = json.dumps(params).encode()
        r = c.post(ANSWER_PATH, content=body, headers={"Authorization": _signed(body)})
        assert r.json()[0]["endpoint"][0]["headers"]["call_uuid"] == "u1"
        # Event webhook: signed, body hash checked
        event = json.dumps({"uuid": "u1", "status": "completed"}).encode()
        assert c.post(
            EVENT_PATH, content=event, headers={"Authorization": _signed(event)}
        ).json() == {"ok": True}
        assert (
            c.post(EVENT_PATH, content=event, headers={"Authorization": _signed()}).status_code
            == 403
        )


def test_the_websocket_requires_the_token_of_its_call(
    deps: Dependencies, vonage_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("building_call.api.main.vonage_not_ready", lambda *_a, **_k: None)
    app = create_app(deps, streaming_factory=lambda *_a: FakeStt())
    with TestClient(app) as c:
        for connected in (
            {"event": "websocket:connected", "content-type": CONTENT_TYPE},
            {
                "event": "websocket:connected",
                "content-type": CONTENT_TYPE,
                "call_uuid": "u1",
                "token": "x",
            },
            {
                "event": "websocket:connected",
                "content-type": "audio/l16;rate=8000",  # not what our NCCO asked for
                "call_uuid": "u1",
                "token": stream_token(SECRET, "u1"),
            },
        ):
            with c.websocket_connect(MEDIA_PATH) as ws:
                ws.send_text(json.dumps(connected))
                with pytest.raises(WebSocketDisconnect) as closing:
                    ws.receive_bytes()
                assert closing.value.code == 1008


# ---------------------------------------------------------------- bridge over Vonage frames


class FakeVonageSocket:
    """Records what the channel sends, as the Vonage side of the socket would receive it."""

    def __init__(self) -> None:
        self.binary: list[bytes] = []
        self.text: list[dict[str, Any]] = []
        self.closed_with: int | None = None

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(data)

    async def send_text(self, data: str) -> None:
        self.text.append(json.loads(data))

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def test_a_whole_call_over_vonage_frames(deps: Dependencies) -> None:
    manager = CallManager(deps)
    stt, tts, sock = FakeStt(), FakeTts(), FakeVonageSocket()
    channel = VonageChannel(sock)  # type: ignore[arg-type]
    bridge = CallBridge(
        manager, lambda _r, _v, _lang: stt, tts, channel, vocabulary=[], phone="+34600"
    )
    call_id = await bridge.begin()

    # Greeting: linear16 16 kHz binary frames of 640 bytes, then a notify marker
    assert sock.binary and all(len(f) == 640 for f in sock.binary)
    assert sock.text[-1] == {"action": "notify", "payload": {"mark": "turno-0"}}

    # Caller audio: linear16 passes straight to the STT (no μ-law decode)
    frame = _tone16k(20)
    await bridge.receive_audio(frame)
    assert bytes(stt.audio) == frame

    # Vonage confirms the marker played → delivery completed
    await bridge.mark_played("turno-0")
    assert (await manager.get(call_id))["last_delivery"]["state"] == "completed"

    await stt.queue.put(TurnEvent("final", "Calle Mayor 12"))
    await _wait(lambda: len(tts.texts) == 2)

    # Barge-in → {"action": "clear"}
    await stt.queue.put(TurnEvent("partial", "sí claro"))
    await _wait(lambda: {"action": "clear"} in sock.text)
    assert (await manager.get(call_id))["last_delivery"]["state"] == "interrupted"

    # DTMF is recorded, not acted on
    await bridge.receive_dtmf("5")
    await bridge.receive_dtmf("not-a-digit")
    assert bridge.dtmf == ["5"]

    for text in ["sí", "3 B", "Gotea el grifo", "sí"]:
        n = len(tts.texts)
        await stt.queue.put(TurnEvent("final", text))
        await _wait(lambda n=n: len(tts.texts) > n or bridge.finished)
    assert bridge.finished
    await bridge.mark_played(sock.text[-1]["payload"]["mark"])
    assert sock.closed_with == 1000  # intentional close: Vonage ends the call
    await bridge.close()


def test_the_answer_url_chooses_the_call_language(
    deps: Dependencies, vonage_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vonage does not send a language: the number's Answer URL carries it (?language=en)."""
    from building_call.telephony.vonage import call_language, ncco_unavailable

    assert call_language({"language": "en-US"}) == "en"
    assert call_language({"language": "xx"}) == call_language({})  # unknown: the default
    apology = ncco_unavailable("en")[0]
    assert apology["language"] == "en-US" and "Sorry" in apology["text"]
    assert ncco_unavailable("es")[0]["language"] == "es-ES"

    monkeypatch.setattr("building_call.api.main.vonage_not_ready", lambda *_a, **_k: None)
    app = create_app(deps, streaming_factory=lambda *_a: FakeStt())
    with TestClient(app) as c:
        params = {"from": "+15550000000", "uuid": "u2", "language": "en"}
        ncco = c.get(ANSWER_PATH, params=params, headers={"Authorization": _signed()}).json()
        assert ncco[0]["endpoint"][0]["headers"]["language"] == "en"


async def test_the_bridge_gets_the_language_from_the_websocket(deps: Dependencies) -> None:
    """The language in the NCCO headers comes back in websocket:connected and reaches the
    bridge, so STT, TTS and the graph run in that language."""
    seen: list[tuple[Any, ...]] = []

    async def factory(channel: Any, phone: Any, existing: Any, language: Any) -> Any:
        seen.append((phone, existing, language))
        raise RuntimeError("stop here")

    from types import SimpleNamespace

    from fastapi import FastAPI

    from building_call.telephony.vonage import create_vonage_router

    app = FastAPI()
    s = SimpleNamespace(vonage_signature_secret=SECRET, public_url="https://bot.example")
    app.include_router(create_vonage_router(s, factory, lambda: None))
    with TestClient(app) as c, c.websocket_connect(MEDIA_PATH) as ws:
        ws.send_text(
            json.dumps(
                {
                    "event": "websocket:connected",
                    "content-type": CONTENT_TYPE,
                    "call_uuid": "u3",
                    "token": stream_token(SECRET, "u3"),
                    "phone": "+15550000000",
                    "language": "en",
                }
            )
        )
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    assert seen == [("+15550000000", None, "en")]


#: Vonage TTS `talk` codes (its supported-languages table, read 2026-11-24). Urdu has no
#: Vonage voice: None means the apology falls back to the default language.
VONAGE_TALK = {"es": "es-ES", "en": "en-US", "ca": "ca-ES", "ar": "ar", "hi": "hi-IN", "ur": None}


@pytest.mark.parametrize("code", i18n.supported())
def test_the_apology_is_said_in_every_language_vonage_can_speak(code: str) -> None:
    from building_call.telephony.vonage import call_language, ncco_unavailable

    assert code in VONAGE_TALK, f"say which Vonage talk language {code} uses (or None)"
    assert call_language({"language": code}) == code
    talk = ncco_unavailable(code)[0]
    expected = VONAGE_TALK[code]
    if expected is None:
        default = i18n.get(None)
        assert talk["language"] == default.tts.vonage_talk_language
        assert talk["text"] == default.phrases.service_unavailable
    else:
        assert talk["language"] == expected
        assert talk["text"] == i18n.get(code).phrases.service_unavailable
