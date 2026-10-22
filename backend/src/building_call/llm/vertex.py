"""Cliente Google Vertex AI (Gemini) procesando SOLO en Europa.

Misma interfaz que `WorkersAIModel` para poder comparar en `evals/compare_models.py`.
Ubicación `eu` = multi-región UE (`aiplatform.eu.rep.googleapis.com`); también se admiten
regiones `europe-*`. Cualquier otra se rechaza para no sacar datos de Europa por error.
Gemini 2.5 con `thinkingBudget=0`: sin razonamiento, que añade latencia sin mejorar el JSON.
Sin `responseSchema` (como con Ollama): el esquema se describe en el prompt y se valida en código.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from building_call.llm.ollama import LLMError, _extract_json, _validate, describe_schema

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def validate_location(location: str) -> str:
    if location == "eu" or location.startswith("europe-"):
        return location
    raise ValueError(
        f"Ubicación de Vertex AI no europea rechazada: {location!r}. Use 'eu' o 'europe-*'"
    )


def generation_url(project: str, location: str, model: str) -> str:
    location = validate_location(location)
    host = (
        "https://aiplatform.eu.rep.googleapis.com"
        if location == "eu"
        else f"https://{location}-aiplatform.googleapis.com"
    )
    return (
        f"{host}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )


def load_credentials(json_path: str = "") -> Any:
    """Cuenta de servicio desde `json_path`, o credenciales por defecto (ADC) si va vacía."""
    import google.auth
    from google.oauth2 import service_account

    if json_path:
        # google-auth no tipa este constructor
        return service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            json_path, scopes=[SCOPE]
        )
    credentials, _ = google.auth.default(scopes=[SCOPE])
    return credentials


def _refresh(credentials: Any) -> None:
    from google.auth.transport.requests import Request

    credentials.refresh(Request())


class VertexModel:
    def __init__(
        self,
        project: str,
        model: str,
        location: str = "eu",
        credentials: Any = None,
        credentials_path: str = "",
        timeout_s: float = 30.0,
        max_tokens: int = 160,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not project:
            raise ValueError("Vertex AI requiere BC_VERTEX_PROJECT")
        self.model = model
        self.name = f"vertex/{model}"
        self.max_tokens = max_tokens
        self._url = generation_url(project, location, model)
        self._credentials = credentials
        self._credentials_path = credentials_path
        self._mutex = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def _token(self) -> str:
        if self._credentials is not None and self._credentials.valid:
            return str(self._credentials.token)
        async with self._mutex:  # un solo refresco aunque lleguen varios turnos a la vez
            if self._credentials is None:
                self._credentials = await asyncio.to_thread(
                    load_credentials, self._credentials_path
                )
            if not self._credentials.valid:
                await asyncio.to_thread(_refresh, self._credentials)
            return str(self._credentials.token)

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
        config: dict[str, Any] = {
            "temperature": 0,
            "maxOutputTokens": self.max_tokens,
            "responseMimeType": "application/json",
        }
        if self.model.startswith("gemini-2.5"):
            config["thinkingConfig"] = {"thinkingBudget": 0}
        body = {
            "systemInstruction": {"parts": [{"text": content_text}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        try:
            token = await self._token()
            r = await self._client.post(
                self._url, json=body, headers={"Authorization": f"Bearer {token}"}
            )
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Error llamando a Vertex AI: {e}") from e
        except Exception as e:  # noqa: BLE001 - errores de google-auth sin exponer el token
            raise LLMError(f"Error de credenciales de Vertex AI: {type(e).__name__}") from e
        if r.status_code != 200:
            error = data.get("error", data) if isinstance(data, dict) else data
            raise LLMError(f"Vertex AI error {r.status_code}: {error}")
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, TypeError):
            text = ""
        if not text.strip():
            raise LLMError("Vertex AI no devolvió contenido (respuesta vacía o bloqueada)")
        return _validate(_extract_json(text), schema)

    async def available(self) -> bool:
        return True

    async def warm_up(self) -> None:
        return None
