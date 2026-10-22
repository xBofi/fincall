"""Cliente Vertex AI: solo Europa, JSON validado y token reutilizado mientras sea válido."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from building_call.llm.ollama import LLMError
from building_call.llm.vertex import VertexModel, generation_url, validate_location

SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string"}, "urgent": {"type": "boolean"}},
}


class FakeCredentials:
    def __init__(self) -> None:
        self.refreshes = 0
        self.token: str | None = None

    @property
    def valid(self) -> bool:
        return self.token is not None

    def refresh(self, _request: Any) -> None:
        self.refreshes += 1
        self.token = f"token-{self.refreshes}"


def _response(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def _model(
    handler: Any, credentials: Any, model: str = "gemini-2.5-flash-lite"
) -> VertexModel:
    return VertexModel(
        "mi-proyecto",
        model,
        credentials=credentials,
        transport=httpx.MockTransport(handler),
    )


def test_url_multi_region_eu() -> None:
    assert generation_url("p", "eu", "gemini-2.5-flash-lite") == (
        "https://aiplatform.eu.rep.googleapis.com/v1/projects/p/locations/eu"
        "/publishers/google/models/gemini-2.5-flash-lite:generateContent"
    )


def test_url_european_region() -> None:
    assert generation_url("p", "europe-southwest1", "m").startswith(
        "https://europe-southwest1-aiplatform.googleapis.com/v1/projects/p/locations/europe-southwest1/"
    )


@pytest.mark.parametrize("location", ["us-central1", "global", "us", "asia-northeast1", ""])
def test_rejects_non_european_location(location: str) -> None:
    with pytest.raises(ValueError, match="no europea"):
        validate_location(location)
    with pytest.raises(ValueError):
        VertexModel("p", "m", location=location, credentials=FakeCredentials())


async def test_extracts_json_and_disables_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refresh", lambda c: c.refresh(None))
    http_requests: list[httpx.Request] = []

    def handler(request_body: httpx.Request) -> httpx.Response:
        http_requests.append(request_body)
        return httpx.Response(200, json=_response('{"intent": "breakdown", "urgent": true}'))

    llm = _model(handler, FakeCredentials())
    assert await llm.extract("system", "Hay una fuga", SCHEMA) == {
        "intent": "breakdown",
        "urgent": True,
    }
    body = json.loads(http_requests[0].content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert body["contents"][0]["parts"][0]["text"] == "Hay una fuga"
    assert http_requests[0].headers["Authorization"] == "Bearer token-1"
    assert http_requests[0].url.host == "aiplatform.eu.rep.googleapis.com"


async def test_token_is_reused_while_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refresh", lambda c: c.refresh(None))
    credentials = FakeCredentials()

    def handler(_http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response('{"intencion": "x"}'))

    llm = _model(handler, credentials)
    for _ in range(3):
        await llm.extract("s", "u", SCHEMA)
    assert credentials.refreshes == 1

    credentials.token = None  # caduca
    await llm.extract("s", "u", SCHEMA)
    assert credentials.refreshes == 2


async def test_http_error_and_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refresh", lambda c: c.refresh(None))
    responses = iter(
        [
            httpx.Response(429, json={"error": {"code": 429, "message": "Resource exhausted"}}),
            httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}),
        ]
    )
    llm = _model(lambda _p: next(responses), FakeCredentials())
    with pytest.raises(LLMError, match="429"):
        await llm.extract("s", "u", SCHEMA)
    with pytest.raises(LLMError, match="no devolvió contenido"):
        await llm.extract("s", "u", SCHEMA)


async def test_gemini_3_does_not_send_thinking_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refresh", lambda c: c.refresh(None))
    bodies: list[dict[str, Any]] = []

    def handler(request_body: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request_body.content))
        return httpx.Response(200, json=_response('{"intencion": "x"}'))

    await _model(handler, FakeCredentials(), "gemini-3.1-flash-lite").extract(
        "s", "u", SCHEMA
    )
    assert "thinkingConfig" not in bodies[0]["generationConfig"]
