"""Telefonía: conversión de audio, puente proveedor-agnóstico, adaptador Twilio y autoconfiguración."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import struct
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.services import CallManager
from building_call.settings import settings as global_settings
from building_call.stt_streaming import TurnEvent
from building_call.telephony import audio
from building_call.telephony.bridge import CallBridge
from building_call.telephony.configure import configure_number, not_ready_reason
from building_call.telephony.twilio import (
    VOICE_PATH,
    twiml_connect_stream,
    valid_signature,
    ws_url_from_public,
)

# ---------------------------------------------------------------- audio


def _tone(rate: int, ms: int, hz: float = 440.0, amplitude: int = 8000) -> bytes:
    n = int(rate * ms / 1000)
    return struct.pack(
        f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * hz * i / rate)) for i in range(n))
    )


def test_phone_input_converts_ulaw_8k_to_pcm16_16k() -> None:
    ulaw = audio.pcm16_to_ulaw(_tone(8000, 100))
    pcm = audio.PhoneInput().convert(ulaw)
    assert abs(len(pcm) - 100 * 16000 // 1000 * 2) <= 4  # 100 ms a 16 kHz
    assert audio.energy(pcm) > audio.energy(b"\x00" * 3200) == 0


def test_phone_output_splits_into_20ms_and_pads_the_end() -> None:
    output = audio.PhoneOutput(22050)
    chunks = output.convert(_tone(22050, 55), final=True)
    assert all(len(t) == 160 for t in chunks)
    assert len(chunks) == 3  # 55 ms → 2 trozos completos + 1 relleno
    assert chunks[-1].endswith(b"\xff")  # silencio μ-law


# ---------------------------------------------------------------- puente


class FakeStt:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[TurnEvent | None] = asyncio.Queue()
        self.audio = bytearray()
        self.closed = False

    async def open(self) -> None: ...

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def events(self):  # type: ignore[no-untyped-def]
        while (ev := await self.queue.get()) is not None:
            yield ev

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)


class FakeTts:
    name = "fake"
    rate = 16000

    def __init__(self) -> None:
        self.texts: list[str] = []

    def available(self) -> bool:
        return True

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return _tone(16000, 40)  # 2 trozos de 20 ms


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.marks: list[str] = []
        self.cleanups = 0
        self.hung_up = False

    async def send_audio(self, ulaw: bytes) -> None:
        self.sent.append(ulaw)

    async def mark(self, name: str) -> None:
        self.marks.append(name)

    async def clean_up(self) -> None:
        self.cleanups += 1

    async def hang_up(self) -> None:
        self.hung_up = True


@pytest.fixture
def manager(deps: Dependencies) -> CallManager:
    return CallManager(deps)


async def _wait(cond, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condición no alcanzada")


async def test_bridge_greets_answers_and_hangs_up(manager: CallManager) -> None:
    stt, tts, channel = FakeStt(), FakeTts(), FakeChannel()
    bridge = CallBridge(
        manager, lambda _rate, _vocab: stt, tts, channel, vocabulary=[], phone="+34600000000"
    )
    call_id = await bridge.begin()
    # Saludo sintetizado y enviado en μ-law con marca del turno 0
    assert len(tts.texts) == 1 and channel.marks == ["turno-0"]
    assert channel.sent and all(len(t) == 160 for t in channel.sent)
    assert (await manager.get(call_id))["caller_phone"] == "+34600000000"

    # El vecino habla: el audio llega al STT convertido a 16 kHz
    await bridge.receive_ulaw(audio.pcm16_to_ulaw(_tone(8000, 20)))
    assert abs(len(stt.audio) - 640) <= 4  # 20 ms a 16 kHz (ratecv redondea)

    await bridge.mark_played("turno-0")
    assert (await manager.get(call_id))["last_delivery"]["state"] == "completed"

    # Turno final del STT → grafo → nueva locución
    await stt.queue.put(TurnEvent("final", "Calle Mayor 12"))
    await _wait(lambda: len(tts.texts) == 2)
    assert channel.marks[-1] == "turno-1"
    assert (await manager.get(call_id))["turn"] == 1
    metrics = manager.metrics.turns(call_id)
    assert metrics and metrics[-1].stt_client == "phone"

    # Barge-in: parcial de ≥2 palabras mientras suena el bot → clear y entrega interrumpida
    await stt.queue.put(TurnEvent("partial", "sí claro"))
    await _wait(lambda: channel.cleanups == 1)
    assert (await manager.get(call_id))["last_delivery"]["state"] == "interrupted"

    # Conversación hasta el final: al reproducirse la despedida se cuelga
    for text in ["sí", "3 B", "Gotea el grifo", "sí"]:
        n = len(tts.texts)
        await stt.queue.put(TurnEvent("final", text))
        await _wait(lambda n=n: len(tts.texts) > n or bridge.finished)
    assert bridge.finished
    await bridge.mark_played(channel.marks[-1])
    assert channel.hung_up
    await bridge.close()
    assert stt.closed


async def test_bridge_saves_both_voices_and_the_mix(manager: CallManager, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from building_call.audio_store import AudioStore

    repo = manager.deps.repo
    store = AudioStore(tmp_path, "secret", retention_days=14, url_seconds=60)
    stt, tts, channel = FakeStt(), FakeTts(), FakeChannel()
    bridge = CallBridge(
        manager, lambda _t, _v: stt, tts, channel, vocabulary=[], phone=None,
        repo=repo, audio_store=store,
    )  # fmt: skip
    cid = await bridge.begin()
    await bridge.receive_ulaw(audio.pcm16_to_ulaw(_tone(8000, 200)))
    await bridge.close()
    tracks = {p["channel"]: p for p in await repo.list_tracks(cid)}
    assert sorted(tracks) == ["assistant", "client", "mix"]
    # La mezcla es lo que se escucha para seguir la llamada: cubre a las dos voces
    assert tracks["mix"]["duration_ms"] >= max(
        tracks["client"]["duration_ms"], tracks["assistant"]["duration_ms"]
    )


# ---------------------------------------------------------------- twilio


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    base_string = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()


def test_twilio_signature_and_twiml() -> None:
    params = {"From": "+34600", "CallSid": "CA1"}
    url = "https://bot.example/api/telephony/twilio/voice"
    assert valid_signature("tok", url, params, _sign("tok", url, params))
    assert not valid_signature("tok", url, params, _sign("other", url, params))
    assert not valid_signature("tok", url, params, None)
    xml = twiml_connect_stream("wss://bot.example/media", {"phone": "+34<600"})
    assert '<Stream url="wss://bot.example/media">' in xml and 'value="+34&lt;600"' in xml
    assert (
        ws_url_from_public("https://bot.example/")
        == "wss://bot.example/api/telephony/twilio/media"
    )


@pytest.fixture
async def client(deps: Dependencies, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "telephony", "twilio")
    monkeypatch.setattr(global_settings, "public_url", "https://bot.example")
    monkeypatch.setattr(global_settings, "twilio_account_sid", "AC1")
    monkeypatch.setattr(global_settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(global_settings, "tts", "none")
    app = create_app(deps, streaming_factory=lambda _t, _v: FakeStt())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


async def test_twilio_webhook_rejects_invalid_signature_and_warns_if_not_ready(
    client: AsyncClient,
) -> None:
    params = {"From": "+34600", "CallSid": "CA1"}
    r = await client.post(VOICE_PATH, data=params, headers={"X-Twilio-Signature": "bad"})
    assert r.status_code == 403
    url = "https://bot.example" + VOICE_PATH
    r = await client.post(
        VOICE_PATH, data=params, headers={"X-Twilio-Signature": _sign("tok", url, params)}
    )
    assert r.status_code == 200 and "<Say" in r.text and "<Hangup/>" in r.text  # sin TTS: no listo
    health = (await client.get("/api/telephony/twilio/health")).json()
    assert health["ok"] is False and "TTS" in health["reason"]
    assert (await client.get("/api/telephony/state")).status_code == 401  # protegido


async def test_twilio_webhook_connects_the_stream_when_ready(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("building_call.api.main.not_ready_reason", lambda *_a, **_k: None)
    params = {"From": "+34600", "CallSid": "CA1"}
    url = "https://bot.example" + VOICE_PATH
    r = await client.post(
        VOICE_PATH, data=params, headers={"X-Twilio-Signature": _sign("tok", url, params)}
    )
    assert r.status_code == 200
    assert '<Stream url="wss://bot.example/api/telephony/twilio/media">' in r.text
    assert '<Parameter name="phone" value="+34600"/>' in r.text


def test_not_ready_reason_explains_what_is_missing() -> None:
    s = SimpleNamespace(
        telephony="twilio", public_url="http://x", twilio_account_sid="", twilio_auth_token=""
    )
    assert "https" in str(not_ready_reason(s, True, True))
    s.public_url = "https://x"
    assert "TWILIO" in str(not_ready_reason(s, True, True))
    s.twilio_account_sid = s.twilio_auth_token = "a"
    assert "ASSEMBLYAI" in str(not_ready_reason(s, True, False))
    assert "TTS" in str(not_ready_reason(s, False, True))
    assert not_ready_reason(s, True, True) is None


async def test_configure_number_sets_the_webhooks() -> None:
    http_requests: list[tuple[str, str, dict[str, Any]]] = []

    def respond(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200, json={"incoming_phone_numbers": [{"sid": "PN1", "phone_number": "+34911"}]}
            )
        data = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        http_requests.append((req.method, str(req.url), data))
        return httpx.Response(
            200,
            json={
                "phone_number": "+34911",
                "voice_url": data["VoiceUrl"].replace("%3A", ":").replace("%2F", "/"),
                "status_callback": "x",
            },
        )

    s = SimpleNamespace(
        twilio_account_sid="AC1",
        twilio_auth_token="tok",
        twilio_number="+34911",
        public_url="https://bot.example/",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as c:
        summary = await configure_number(s, c)
    assert summary["number"] == "+34911"
    assert summary["voice_url"] == "https://bot.example/api/telephony/twilio/voice"
    assert http_requests[0][1].endswith("/IncomingPhoneNumbers/PN1.json")
    assert http_requests[0][2]["VoiceMethod"] == "POST"


def test_twilio_json_event_decodes_as_expected() -> None:
    # Formato de referencia de Media Streams; si Twilio lo cambia, este test lo detecta antes que una llamada real
    m = json.loads(
        '{"event":"media","streamSid":"MZ1","media":{"track":"inbound","payload":"//8="}}'
    )
    assert base64.b64decode(m["media"]["payload"]) == b"\xff\xff"


class FailingCallManager(CallManager):
    """Gestor real cuyo turno revienta (LLM caído, timeout...) las veces indicadas."""

    def __init__(self, deps: Dependencies, failures: int) -> None:
        super().__init__(deps)
        self.failures = failures

    async def message(self, call_id: str, text: str, stt_ms: int | None = None):  # type: ignore[no-untyped-def]
        if self.failures:
            self.failures -= 1
            raise RuntimeError("LLM caído")
        return await super().message(call_id, text, stt_ms)


async def test_bridge_asks_to_repeat_if_a_turn_fails(deps: Dependencies) -> None:
    manager = FailingCallManager(deps, failures=1)
    stt, tts, channel = FakeStt(), FakeTts(), FakeChannel()
    bridge = CallBridge(manager, lambda _t, _v: stt, tts, channel, vocabulary=[], phone=None)
    call_id = await bridge.begin()
    await stt.queue.put(TurnEvent("final", "Calle Mayor 12"))
    await _wait(lambda: len(tts.texts) == 2)
    assert "repet" in tts.texts[-1].lower()
    assert not bridge.finished and not channel.hung_up
    # El siguiente turno funciona con normalidad
    await stt.queue.put(TurnEvent("final", "Calle Mayor 12"))
    await _wait(lambda: manager.get_local(call_id)["turn"] == 1)
    await bridge.close()


async def test_bridge_closes_with_apology_and_leaves_the_call_for_review(
    deps: Dependencies,
) -> None:
    manager = FailingCallManager(deps, failures=2)
    stt, tts, channel = FakeStt(), FakeTts(), FakeChannel()
    bridge = CallBridge(manager, lambda _t, _v: stt, tts, channel, vocabulary=[], phone=None)
    call_id = await bridge.begin()
    for _ in range(2):
        n = len(tts.texts)
        await stt.queue.put(TurnEvent("final", "Calle Mayor 12"))
        await _wait(lambda n=n: len(tts.texts) > n)
    assert bridge.finished
    await bridge.mark_played(channel.marks[-1])
    assert channel.hung_up
    saved = await deps.repo.get_call(call_id)  # type: ignore[attr-defined]
    assert saved.state == "finished" and saved.needs_review
    assert saved.review_reason
    await bridge.close()


async def test_bridge_apologises_if_the_stt_drops(manager: CallManager) -> None:
    stt, tts, channel = FakeStt(), FakeTts(), FakeChannel()
    bridge = CallBridge(manager, lambda _t, _v: stt, tts, channel, vocabulary=[], phone=None)
    call_id = await bridge.begin()
    await stt.queue.put(None)  # AssemblyAI cierra el stream sin que nadie haya colgado
    await _wait(lambda: bridge.finished)
    assert len(tts.texts) == 2
    assert (await manager.get(call_id))["finished"]
    await bridge.close()


def test_the_twilio_stream_requires_its_call_token(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión de seguridad 2026-10-22: cualquiera podía abrir el WebSocket de medios y hablar
    con el bot (gastando STT, LLM y TTS). Ahora el stream lleva un token HMAC de su CallSid que
    solo pone nuestro webhook firmado."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from building_call.telephony.twilio import MEDIA_PATH, token_stream

    monkeypatch.setattr(global_settings, "twilio_auth_token", "tok")
    monkeypatch.setattr("building_call.api.main.not_ready_reason", lambda *_a, **_k: None)
    opened_log: list[str] = []
    app = create_app(deps, streaming_factory=None)
    with TestClient(app) as c:
        for parameters in ({}, {"call_sid": "CA1", "token": "fake"}):
            with c.websocket_connect(MEDIA_PATH) as ws:
                ws.send_json(
                    {
                        "event": "start",
                        "start": {"streamSid": "MZ1", "customParameters": parameters},
                    }
                )
                with pytest.raises(WebSocketDisconnect) as closing:
                    ws.receive_json()
                assert closing.value.code == 1008
                opened_log.append("closed")
    assert opened_log == ["closed", "closed"]
    assert token_stream("tok", "CA1") != token_stream("tok", "CA2")
    assert token_stream("tok", "CA1") != token_stream("other", "CA1")


async def test_the_webhook_signs_the_stream_with_the_callsid(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from building_call.telephony.twilio import token_stream

    monkeypatch.setattr("building_call.api.main.not_ready_reason", lambda *_a, **_k: None)
    params = {"From": "+34600", "CallSid": "CA1"}
    url = "https://bot.example" + VOICE_PATH
    r = await client.post(
        VOICE_PATH, data=params, headers={"X-Twilio-Signature": _sign("tok", url, params)}
    )
    assert f'<Parameter name="token" value="{token_stream("tok", "CA1")}"/>' in r.text
