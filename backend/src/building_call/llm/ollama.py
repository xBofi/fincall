"""Cliente Ollama local con salida JSON.

Nota operativa (Ollama 0.23.x + qwen3.5): `format` con JSON Schema y `think=false` deja la
petición colgada hasta el timeout, y con `think=true` el modelo razona decenas de miles de
tokens. `format="json"` + `think=false` responde en ~1 s. El esquema se usa aquí solo para
describir las claves en el prompt y validar la respuesta en código.

Latencia: el prompt de sistema va primero y es estable por tarea para que Ollama reutilice la
caché KV del prefijo; el contexto variable va al final. `num_predict` acota la salida.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMError(RuntimeError):
    pass


def _extract_json(content_text: str) -> dict[str, Any]:
    text = content_text.strip()
    m = _CODE_FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise LLMError(f"El modelo no devolvió JSON: {content_text[:200]!r}") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON inválido del modelo: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("La respuesta del modelo no es un objeto JSON")
    return data


def describe_schema(schema: dict[str, Any], omit_nulls: bool = True) -> str:
    props: dict[str, Any] = schema.get("properties", {})
    lines = []
    for key, spec in props.items():
        kind = spec.get("type", "string")
        kind_txt = " o ".join(kind) if isinstance(kind, list) else str(kind)
        if "enum" in spec:
            kind_txt += " (uno de: " + ", ".join(map(str, spec["enum"])) + ")"
        lines.append(f'- "{key}": {kind_txt}')
    header = "Responde únicamente con un objeto JSON. Claves posibles:\n"
    footer = (
        "\nOmite las claves cuyo valor sería null: incluye solo lo que el texto diga."
        if omit_nulls
        else "\nIncluye todas las claves."
    )
    return header + "\n".join(lines) + footer


def _validate(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = schema.get("properties", {})
    output: dict[str, Any] = {}
    for key, spec in props.items():
        value = data.get(key)
        if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "n/a"}:
            value = None
        if "enum" in spec and value not in spec["enum"]:
            value = None
        output[key] = value
    return output


class OllamaModel:
    def __init__(
        self,
        url: str,
        model: str,
        timeout_s: float = 120.0,
        num_predict: int = 160,
        num_ctx: int = 2048,
        keep_alive: str = "30m",
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.name = f"ollama/{model}"
        self.num_predict = num_predict
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        """`instruccion_formato=None` describe el esquema automáticamente; "" indica que el
        prompt de sistema ya incluye el formato."""
        if format_instruction is None:
            format_instruction = describe_schema(schema)
        content_text = f"{system}\n\n{format_instruction}" if format_instruction else system
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": content_text},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }
        try:
            r = await self._client.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
            content_text = r.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"Error llamando a Ollama: {e}") from e
        return _validate(_extract_json(content_text), schema)

    async def available(self) -> bool:
        try:
            r = await self._client.get(f"{self.url}/api/tags")
            models = r.json().get("models", [])
            return r.status_code == 200 and any(m["name"] == self.model for m in models)
        except (httpx.HTTPError, ValueError):
            return False

    async def warm_up(self) -> None:
        """Carga el modelo en GPU para que el primer turno real no pague la carga."""
        try:
            await self.extract(
                "Devuelve JSON.",
                "hola",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )
        except LLMError:
            pass
