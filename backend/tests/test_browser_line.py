"""The phone line in the browser (no Twilio): the console's WebSocket speaks Media Streams and
reaches the same bridge as a phone call. Also the Twilio browser call's identity rules."""

from __future__ import annotations

import base64
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from session_helpers import make_session
from test_telephony import FakeStt, FakeTts, _tone

from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.stt_streaming import TurnEvent
from building_call.telephony import audio
from building_call.telephony.browser import console_identity
from building_call.telephony.browser_line import LINE_PATH


class SpeakingStt(FakeStt):
    """Hears "Calle Mayor 12" once audio arrives (events are queued on the app's own loop)."""

    async def send_audio(self, pcm: bytes) -> None:
        first = not self.audio
        await super().send_audio(pcm)
        if first:
            await self.queue.put(TurnEvent("final", "Calle Mayor 12"))


@pytest.fixture
def line(deps: Dependencies, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeStt, FakeTts]:
    stt, tts = SpeakingStt(), FakeTts()
    monkeypatch.setattr("building_call.api.main.create_tts", lambda _s: tts)
    app = create_app(deps, streaming_factory=lambda _rate, _vocab, _lang: stt)
    client = TestClient(app)
    client.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
    return client, stt, tts


def _wait(condition: Any, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        time.sleep(0.02)


def test_the_console_talks_to_the_agent_through_the_phone_path(
    line: tuple[TestClient, FakeStt, FakeTts],
) -> None:
    client, stt, tts = line
    assert client.get("/api/health").json()["browser_line"] is True
    call_id = client.post("/api/calls", json={}).json()["call_id"]

    with client.websocket_connect(LINE_PATH) as ws:
        ws.send_json({"event": "start", "start": {"customParameters": {"call_id": call_id}}})
        # The greeting comes back as μ-law frames of 20 ms, then a mark
        media = ws.receive_json()
        assert media["event"] == "media"
        assert len(base64.b64decode(media["media"]["payload"])) == 160
        events = [ws.receive_json() for _ in range(2)]
        mark = next(e for e in [media, *events] if e["event"] == "mark")
        assert mark["mark"]["name"] == "turno-0"
        assert tts.texts and tts.texts[0].startswith("Hola")

        # The caller speaks: μ-law in, PCM16 16 kHz reaches the STT
        frame = audio.pcm16_to_ulaw(_tone(8000, 20))
        ws.send_json({"event": "media", "media": {"payload": base64.b64encode(frame).decode()}})
        ws.send_json({"event": "mark", "mark": {"name": "turno-0"}})
        _wait(lambda: len(stt.audio) > 0 and len(tts.texts) >= 2)
        assert "Calle Mayor 12" in tts.texts[-1]
        ws.send_json({"event": "stop"})

    state = client.get(f"/api/calls/{call_id}").json()
    assert state["history"][1] == {"role": "caller", "text": "Calle Mayor 12"}


def test_the_line_needs_a_session_and_an_open_call(
    line: tuple[TestClient, FakeStt, FakeTts],
) -> None:
    client, _, _ = line
    with client.websocket_connect(LINE_PATH) as ws:
        ws.send_json({"event": "start", "start": {"customParameters": {"call_id": "llam-nope"}}})
        assert ws.receive_json()["event"] == "error"
    client.cookies.clear()
    with client.websocket_connect(LINE_PATH) as ws:
        with pytest.raises(Exception):  # noqa: B017 - closed with 1008 before any message
            ws.receive_json()


@pytest.mark.parametrize(
    ("username", "identity"),
    [("ann", "console_ann"), ("ana.lópez@x.es", "console_ana_l_pez_x_es")],
)
def test_twilio_identities_use_only_letters_digits_and_underscores(
    username: str, identity: str
) -> None:
    # Twilio Access Token identities: alphanumeric and underscore only
    assert console_identity(username) == identity

