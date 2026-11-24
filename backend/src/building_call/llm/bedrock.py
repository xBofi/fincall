"""AWS Bedrock client: Converse API with structured output, ONLY in European regions.

Same interface as `VertexModel`, so it can be compared in `evals/compare_models.py`
(`bedrock:<model>`). Shapes from the official docs (docs.aws.amazon.com, read 2026-11-24):

- `POST https://bedrock-runtime.{region}.amazonaws.com/model/{modelId}/converse`
- Bedrock API key as `Authorization: Bearer <key>` (no SigV4, so no boto3 dependency).
- `outputConfig.textFormat = {type: "json_schema", structure: {jsonSchema: {schema, name}}}`
  with the schema as a JSON string. It needs `additionalProperties: false`; at most 16
  nullable properties (the interpretation schema has 14). Model support varies: Claude Haiku
  5.5 supports it, Nova Lite does not (set `structured_output=False` for those: the schema is
  then described in the prompt, as with Ollama).

The region must be `eu-*`, and the default model is the EU inference profile
(`eu.anthropic.claude-haiku-5-5`), so requests stay in the EU (RGPD, as with Vertex).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from building_call.llm.ollama import LLMError, _extract_json, _validate, describe_schema
from building_call.llm.strict_schema import schema_name, strict_schema, without_nulls


def validate_region(region: str) -> str:
    if region.startswith("eu-"):
        return region
    raise ValueError(f"Región de Bedrock no europea rechazada: {region!r}. Use 'eu-*'")


class BedrockModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        region: str = "eu-west-1",
        timeout_s: float = 20.0,
        max_tokens: int = 400,
        structured_output: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Bedrock requiere BC_BEDROCK_API_KEY")
        region = validate_region(region)
        self.model = model
        self.name = f"bedrock/{model}"
        self.max_tokens = max_tokens
        self.structured_output = structured_output
        self._url = (
            f"https://bedrock-runtime.{region}.amazonaws.com/model/{quote(model, safe='')}/converse"
        )
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )
        self.last_usage: dict[str, int] = {}

    def request_body(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"maxTokens": self.max_tokens, "temperature": 0},
        }
        if self.structured_output:
            body["system"] = [{"text": system}]
            body["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(strict_schema(schema), ensure_ascii=False),
                            "name": schema_name(schema),
                        }
                    },
                }
            }
        else:
            if format_instruction is None:
                format_instruction = describe_schema(schema)
            text = f"{system}\n\n{format_instruction}" if format_instruction else system
            body["system"] = [{"text": text}]
        return body

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        body = self.request_body(system, user, schema, format_instruction)
        try:
            r = await self._client.post(self._url, json=body)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Error llamando a Bedrock: {type(e).__name__}") from e
        if r.status_code != 200:
            message = data.get("message") if isinstance(data, dict) else None
            raise LLMError(f"Bedrock error {r.status_code}: {message or 'sin detalle'}")
        usage = data.get("usage") or {}
        self.last_usage = {
            "input": int(usage.get("inputTokens", 0)),
            "output": int(usage.get("outputTokens", 0)),
        }
        try:
            parts = data["output"]["message"]["content"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, TypeError):
            text = ""
        if not text.strip():
            raise LLMError(f"Bedrock no devolvió contenido ({data.get('stopReason')!r})")
        return _validate(without_nulls(_extract_json(text)), schema)

    async def available(self) -> bool:
        return True

    async def warm_up(self) -> None:
        return None
