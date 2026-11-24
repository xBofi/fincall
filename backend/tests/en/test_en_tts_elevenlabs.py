"""Voz del bot con ElevenLabs: streaming PCM, caché y endpoint de la consola (sin red)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from building_call.telephony.tts import ElevenLabsSynthesizer, create_tts


def _synthesizer(handler: Any) -> ElevenLabsSynthesizer:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ElevenLabsSynthesizer("key", "voz-1", client=client)


async def test_requests_pcm_16k_in_english_and_delivers_whole_samples() -> None:
    http_requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        http_requests.append(req)
        # 5 bytes: el último es media muestra y no debe salir hasta completarse
        return httpx.Response(200, content=b"\x01\x00\x02\x00\x03")

    tts = _synthesizer(handler)
    chunks = [t async for t in tts.fragments("Hi,   there.")]
    assert all(len(t) % 2 == 0 for t in chunks)
    assert b"".join(chunks) == b"\x01\x00\x02\x00"
    req = http_requests[0]
    assert req.url.path == "/v1/text-to-speech/voz-1/stream"
    assert req.url.params["output_format"] == "pcm_16000"
    assert req.headers["xi-api-key"] == "key"
    assert b'"language_code":"en"' in req.content and b'"text":"Hi, there."' in req.content


async def test_repeated_phrase_comes_from_the_cache() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"\x00\x01" * 10)

    tts = _synthesizer(handler)
    assert await tts.synthesize("Hola") == await tts.synthesize("Hola")
    assert calls == 1


async def test_provider_error_returns_no_audio() -> None:
    tts = _synthesizer(lambda req: httpx.Response(401, json={"detail": "invalid_api_key"}))
    with pytest.raises(RuntimeError, match="401"):
        await tts.synthesize("Hola")


def test_create_tts_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    from building_call.settings import settings

    monkeypatch.setattr(settings, "tts", "elevenlabs")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_voice", "v")
    tts = create_tts(settings)
    assert isinstance(tts, ElevenLabsSynthesizer) and tts.available()
    monkeypatch.setattr(settings, "elevenlabs_voice", "")
    assert not create_tts(settings).available()


class _FakeVoice:
    name = "fake"
    rate = 16000

    def __init__(self, fails: bool = False) -> None:
        self.fails = fails

    def available(self) -> bool:
        return True

    async def synthesize(self, text: str, language: str | None = None) -> bytes:
        if self.fails:
            raise RuntimeError("down")
        return b"\x00\x01" * 4


async def _client_with_voice(deps: Any, voice: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from session_helpers import make_session

    from building_call.api import main

    monkeypatch.setattr(main, "create_tts", lambda settings: voice)
    app = main.create_app(deps, streaming_factory=None)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    c.cookies.set("bc_session", make_session(deps.repo))
    return c


async def test_console_receives_the_server_voice(
    deps: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await _client_with_voice(deps, _FakeVoice(), monkeypatch) as c:
        r = await c.post("/api/voz-bot", json={"text": "Hola"})
    assert r.status_code == 200
    assert r.headers["x-tasa"] == "16000"
    assert r.content == b"\x00\x01" * 4


async def test_voice_down_is_a_502_to_fall_back_to_the_browser(
    deps: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await _client_with_voice(deps, _FakeVoice(fails=True), monkeypatch) as c:
        r = await c.post("/api/voz-bot", json={"text": "Hola"})
    assert r.status_code == 502


def test_ordinals_are_read_as_words() -> None:
    from building_call.telephony.tts import text_for_voice

    assert (
        text_for_voice("On the 1º floor the doors are A or B.")
        == "On the first floor the doors are A or B."
    )
    assert text_for_voice("The 3º  B and the 2ª floor") == "The third B and the second floor"
    assert text_for_voice("12 Main Street") == "12 Main Street"
