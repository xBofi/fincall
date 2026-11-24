"""OpenAI client: Responses API with Structured Outputs (strict JSON schema).

Same interface as `VertexModel`, so it can be compared in `evals/compare_models.py`
(`openai:<model>`). Shapes from the official docs (developers.openai.com, read 2026-11-24):
`POST /v1/responses` with `instructions`, `input`, `max_output_tokens` and
`text.format = {type: "json_schema", name, schema, strict: true}`.

Strict mode turns the project's optional-key schema into all-required nullable keys
(`strict_schema`); the answer goes through `_validate` like every other provider, and the
interpretation through the Pydantic `Interpretation`.

No `temperature`: the default model is a reasoning model and the docs do not say it accepts
sampling parameters. EU data residency is a project setting plus the `eu.api.openai.com` host
(`BC_OPENAI_URL`), only for eligible projects.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from building_call.llm.ollama import LLMError, _validate
from building_call.llm.strict_schema import schema_name, strict_schema, without_nulls


class OpenAIModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com",
        reasoning_effort: str | None = None,
        timeout_s: float = 20.0,
        max_output_tokens: int = 2048,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI requiere BC_OPENAI_API_KEY")
        self.model = model
        self.name = f"openai/{model}"
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self._url = f"{base_url.rstrip('/')}/v1/responses"
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )
        #: Tokens of the last call (input, output), for the cost estimate of the evals
        self.last_usage: dict[str, int] = {}

    def request_body(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name(schema),
                    "schema": strict_schema(schema),
                    "strict": True,
                }
            },
        }
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        return body

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        # The schema travels in `text.format`: no description of it in the prompt
        body = self.request_body(system, user, schema)
        try:
            r = await self._client.post(self._url, json=body)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Error llamando a OpenAI: {type(e).__name__}") from e
        if r.status_code != 200:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            message = error.get("message") if isinstance(error, dict) else None
            raise LLMError(f"OpenAI error {r.status_code}: {message or 'sin detalle'}")
        usage = data.get("usage") or {}
        self.last_usage = {
            "input": int(usage.get("input_tokens", 0)),
            "output": int(usage.get("output_tokens", 0)),
        }
        text, refusal = "", None
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text += part.get("text", "")
                elif part.get("type") == "refusal":
                    refusal = part.get("refusal")
        if refusal:
            raise LLMError(f"OpenAI rechazó la petición: {refusal}")
        if not text.strip():
            raise LLMError(f"OpenAI no devolvió contenido (estado {data.get('status')!r})")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON inválido de OpenAI: {e}") from e
        if not isinstance(parsed, dict):
            raise LLMError("La respuesta de OpenAI no es un objeto JSON")
        return _validate(without_nulls(parsed), schema)

    async def available(self) -> bool:
        return True

    async def warm_up(self) -> None:
        return None
