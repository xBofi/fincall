from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import make_session

from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.stt import Transcript


class FakeSTT:
    name = "falso/stt"

    def __init__(self, text: str = "Calle Mayor 12") -> None:
        self.text = text

    def available(self) -> bool:
        return True

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        return Transcript(
            text=self.text, ms=42, model=self.name, detail={"bytes": len(audio)}
        )


@pytest.fixture
async def client(deps: Dependencies):
    app = create_app(deps, stt=FakeSTT())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
        yield c


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["graphs"] == ["standard"]
    assert body["stt_available"] is True
    assert body["thresholds_ms"]["target"] == 1200


async def test_estates_include_homes(client: AsyncClient) -> None:
    r = await client.get("/api/estates")
    estates = r.json()
    assert {f["id"] for f in estates} == {"finca-mayor-12", "finca-alameda-5"}
    assert "3º B" in next(f for f in estates if f["id"] == "finca-mayor-12")["homes"]


async def test_conversation_via_api(client: AsyncClient, repo: object) -> None:
    r = await client.post("/api/calls", json={"caller_phone": "+34600111222"})
    assert r.status_code == 201
    call_id = r.json()["call_id"]
    assert r.json()["awaiting_user"] is True
    assert r.json()["turn"] == 0

    for text in [
        "Calle Mayor 12",
        "sí",
        "4 A",
        "No funciona el enchufe de la cocina",
        "sí",
    ]:
        r = await client.post(f"/api/calls/{call_id}/messages", json={"text": text})
        assert r.status_code == 200
    view = r.json()
    assert view["finished"] is True
    assert view["turn"] == 5
    assert view["data"]["category"] == "electrical"
    assert view["data"]["incident_id"]
    assert "server_ms" in view["timings"]

    r = await client.get("/api/incidents")
    assert len(r.json()) == 1
    call = repo.calls[call_id]  # type: ignore[attr-defined]
    assert call.state == "finished"
    assert call.finished_at is not None
    assert call.needs_review is False


async def test_audio_turn_uses_server_stt(client: AsyncClient) -> None:
    call_id = (await client.post("/api/calls", json={})).json()["call_id"]
    r = await client.post(
        f"/api/calls/{call_id}/audio",
        files={"file": ("turn.webm", b"\x1a\x45\xdf\xa3 audio falso", "audio/webm")},
        data={"mime": "audio/webm"},
    )
    assert r.status_code == 200
    view = r.json()
    assert view["transcript"] == "Calle Mayor 12"
    assert view["timings"]["stt_ms"] == 42
    assert view["data"]["estate_attempts"] == 0
    assert "Calle Mayor 12" in view["bot_messages"][0]


async def test_client_metrics_and_aggregates(client: AsyncClient) -> None:
    call_id = (await client.post("/api/calls", json={})).json()["call_id"]
    await client.post(f"/api/calls/{call_id}/messages", json={"text": "Calle Mayor 12"})
    r = await client.post(
        f"/api/calls/{call_id}/metrics",
        json={"turn": 1, "response_ms": 950, "vad_silence_ms": 550, "tts_start_ms": 80},
    )
    assert r.status_code == 200
    assert r.json()["rating"] == "target"
    r = await client.get(f"/api/calls/{call_id}/metrics")
    aggregates = r.json()["aggregates"]
    assert aggregates["turns"] == 2  # saludo + primer turno
    assert aggregates["response_ms"]["p50"] == 950
    r = await client.get("/api/metrics")
    assert r.json()["aggregates"]["ratings"]["target"] == 1
    r = await client.post(
        f"/api/calls/{call_id}/metrics", json={"turn": 99, "response_ms": 1}
    )
    assert r.status_code == 404


async def test_nonexistent_call_404(client: AsyncClient) -> None:
    r = await client.post("/api/calls/nope/messages", json={"text": "hola"})
    assert r.status_code == 404
