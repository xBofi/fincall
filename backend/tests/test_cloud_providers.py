"""OpenAI (Responses + strict Structured Outputs) and AWS Bedrock (Converse, EU only), mocked."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from building_call.llm.bedrock import BedrockModel, validate_region
from building_call.llm.interpretation import _schema, interpret
from building_call.llm.ollama import LLMError
from building_call.llm.openai import OpenAIModel
from building_call.llm.strict_schema import strict_schema

SECRET = "sk-test-not-a-real-key"
CATALOG = ["plumbing", "electrical", "unclassifiable"]


def _openai_response(payload: dict[str, Any] | str, **extra: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {"input_tokens": 900, "output_tokens": 40},
        **extra,
    }


def _bedrock_response(text: str) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 800, "outputTokens": 30, "totalTokens": 830},
        "metrics": {"latencyMs": 420},
    }


# --------------------------------------------------------------------------- strict schema


def test_strict_schema_makes_every_key_required_and_nullable() -> None:
    s = strict_schema(_schema(CATALOG))
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])
    assert s["properties"]["intencion"]["type"] == ["string", "null"]
    assert None in s["properties"]["intencion"]["enum"]
    assert s["properties"]["confirma"]["type"] == ["boolean", "null"]
    # Bedrock: at most 16 nullable properties
    assert len(s["properties"]) <= 16


# --------------------------------------------------------------------------- OpenAI


async def test_openai_request_uses_strict_json_schema_and_interprets() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_openai_response(
                {"intencion": "continuar", "problema": "fuga de agua", "categoria": "fontaneria",
                 "urgencia": "alta", "direccion": None, "confirma": None}
            ),
        )  # fmt: skip

    model = OpenAIModel(SECRET, "gpt-6-luna", transport=httpx.MockTransport(handler))
    r = await interpret(model, "¿Qué ocurre?", "Hay una fuga de agua", "collect_request", CATALOG)
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["auth"] == f"Bearer {SECRET}"
    fmt = seen["body"]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["name"] == "interpretation"
    assert fmt["schema"]["additionalProperties"] is False
    assert "temperature" not in seen["body"] and "reasoning" not in seen["body"]
    assert seen["body"]["store"] is False
    assert (r.intent, r.category, r.urgency, r.problem) == (
        "continue", "plumbing", "high", "fuga de agua",
    )  # fmt: skip
    assert r.address is None
    assert model.last_usage == {"input": 900, "output": 40}


async def test_openai_eu_host_and_reasoning_effort() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_response({"intencion": "humano"}))

    model = OpenAIModel(
        SECRET, "gpt-6-luna", base_url="https://eu.api.openai.com/", reasoning_effort="low",
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    out = await model.extract(
        "s", "u", {"title": "t", "properties": {"intencion": {"type": "string"}}}
    )
    assert seen["url"] == "https://eu.api.openai.com/v1/responses"
    assert seen["body"]["reasoning"] == {"effort": "low"}
    assert out == {"intencion": "humano"}


async def test_openai_errors_do_not_leak_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})

    model = OpenAIModel(SECRET, "gpt-6-luna", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as e:
        await model.extract("s", "u", {"properties": {}})
    assert "401" in str(e.value) and SECRET not in str(e.value)


async def test_openai_refusal_and_empty_output() -> None:
    def refusal(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]})  # fmt: skip

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "incomplete", "output": []})

    for handler, text in ((refusal, "rechazó"), (empty, "incomplete")):
        model = OpenAIModel(SECRET, "m", transport=httpx.MockTransport(handler))
        with pytest.raises(LLMError, match=text):
            await model.extract("s", "u", {"properties": {}})


def test_openai_requires_a_key() -> None:
    with pytest.raises(ValueError):
        OpenAIModel("", "gpt-6-luna")


# --------------------------------------------------------------------------- Bedrock


def test_bedrock_only_in_europe() -> None:
    assert validate_region("eu-west-1") == "eu-west-1"
    assert validate_region("eu-south-2") == "eu-south-2"
    with pytest.raises(ValueError):
        validate_region("us-east-1")
    with pytest.raises(ValueError):
        BedrockModel(SECRET, "eu.anthropic.claude-haiku-5-5", region="us-west-2")


async def test_bedrock_converse_with_structured_output() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_bedrock_response(
            '{"intencion":"pregunta","pregunta":"¿Esto se graba?","direccion":null}'))  # fmt: skip

    model = BedrockModel(
        SECRET, "eu.anthropic.claude-haiku-5-5", transport=httpx.MockTransport(handler)
    )
    r = await interpret(model, None, "¿Esto se graba?", "identify_estate")
    assert seen["url"] == (
        "https://bedrock-runtime.eu-west-1.amazonaws.com/model/"
        "eu.anthropic.claude-haiku-5-5/converse"
    )
    assert seen["auth"] == f"Bearer {SECRET}"
    body = seen["body"]
    assert body["messages"][0]["content"][0]["text"].endswith('"""¿Esto se graba?"""')
    assert body["inferenceConfig"] == {"maxTokens": 400, "temperature": 0}
    fmt = body["outputConfig"]["textFormat"]
    assert fmt["type"] == "json_schema"
    schema = json.loads(fmt["structure"]["jsonSchema"]["schema"])
    assert schema["additionalProperties"] is False
    assert (r.intent, r.question) == ("question", "¿Esto se graba?")
    assert model.last_usage == {"input": 800, "output": 30}


async def test_bedrock_model_id_is_url_encoded_and_prompt_schema_fallback() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_bedrock_response('```json\n{"ok": true}\n```'))

    model = BedrockModel(
        SECRET, "eu.amazon.nova-lite-v1:0", structured_output=False,
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    out = await model.extract("Sistema", "hola", {"properties": {"ok": {"type": "boolean"}}})
    assert "/model/eu.amazon.nova-lite-v1%3A0/converse" in seen["url"]
    assert "outputConfig" not in seen["body"]
    assert '"ok"' in seen["body"]["system"][0]["text"]
    assert out == {"ok": True}


async def test_bedrock_errors_do_not_leak_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "The security token is invalid"})

    model = BedrockModel(
        SECRET, "eu.anthropic.claude-haiku-5-5", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LLMError) as e:
        await model.extract("s", "u", {"properties": {}})
    assert "403" in str(e.value) and SECRET not in str(e.value)


def test_create_llm_selects_the_new_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from building_call.api.main import create_llm
    from building_call.settings import settings

    monkeypatch.setattr(settings, "llm", "openai")
    monkeypatch.setattr(settings, "openai_api_key", SECRET)
    assert create_llm().name == "openai/gpt-6-luna"
    monkeypatch.setattr(settings, "llm", "bedrock")
    monkeypatch.setattr(settings, "bedrock_api_key", SECRET)
    assert create_llm().name == "bedrock/eu.anthropic.claude-haiku-5-5"
