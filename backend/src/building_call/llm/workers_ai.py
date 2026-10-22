"""Cliente Cloudflare Workers AI (API compatible con chat completions).

Misma interfaz que `OllamaModel` para poder comparar en `evals/compare_models.py`.
Modelos con razonamiento (qwen3.x): `chat_template_kwargs.enable_thinking=false` evita cientos de
tokens de "reasoning" (medido: 178 tokens/4 s -> 15 tokens/2 s).
"""

from __future__ import annotations

from typing import Any

import httpx

from building_call.llm.ollama import LLMError, _extract_json, _validate, describe_schema


class WorkersAIModel:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str,
        timeout_s: float = 30.0,
        max_tokens: int = 160,
        disable_reasoning: bool = True,
    ) -> None:
        if not account_id or not api_token:
            raise ValueError(
                "Workers AI requiere BC_CLOUDFLARE_ACCOUNT_ID y BC_CLOUDFLARE_API_TOKEN"
            )
        self.model = model
        self.name = f"workers-ai/{model}"
        self.max_tokens = max_tokens
        self.disable_reasoning = disable_reasoning
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self._client = httpx.AsyncClient(
            timeout=timeout_s, headers={"Authorization": f"Bearer {api_token}"}
        )

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        if format_instruction is None:
            format_instruction = describe_schema(schema)
        content_text = f"{system}\n\n{format_instruction}" if format_instruction else system
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": content_text},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.disable_reasoning:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            r = await self._client.post(self._url, json=body)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Error llamando a Workers AI: {e}") from e
        if not data.get("success"):
            raise LLMError(f"Workers AI error: {data.get('errors')}")
        result = data.get("result", {})
        if isinstance(result, dict) and "choices" in result:
            text = result["choices"][0]["message"].get("content")
        elif isinstance(result, dict):
            text = result.get("response")
        else:
            text = result
        if not isinstance(text, str) or not text.strip():
            raise LLMError(
                "Workers AI no devolvió contenido (¿se agotó max_tokens en razonamiento?)"
            )
        return _validate(_extract_json(text), schema)

    async def available(self) -> bool:
        return True

    async def warm_up(self) -> None:
        return None
