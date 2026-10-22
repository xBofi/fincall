"""Cliente Google Vertex AI (Gemini) procesando SOLO en Europa.

Misma interfaz que `ModeloWorkersAI` para poder comparar en `evals/comparar_modelos.py`.
Ubicación `eu` = multi-región UE (`aiplatform.eu.rep.googleapis.com`); también se admiten
regiones `europe-*`. Cualquier otra se rechaza para no sacar datos de Europa por error.
Gemini 2.5 con `thinkingBudget=0`: sin razonamiento, que añade latencia sin mejorar el JSON.
Sin `responseSchema` (como con Ollama): el esquema se describe en el prompt y se valida en código.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from building_call.llm.ollama import LLMError, _extraer_json, _validar, describir_esquema

ALCANCE = "https://www.googleapis.com/auth/cloud-platform"


def validar_ubicacion(ubicacion: str) -> str:
    if ubicacion == "eu" or ubicacion.startswith("europe-"):
        return ubicacion
    raise ValueError(
        f"Ubicación de Vertex AI no europea rechazada: {ubicacion!r}. Use 'eu' o 'europe-*'"
    )


def url_generacion(proyecto: str, ubicacion: str, modelo: str) -> str:
    ubicacion = validar_ubicacion(ubicacion)
    host = (
        "https://aiplatform.eu.rep.googleapis.com"
        if ubicacion == "eu"
        else f"https://{ubicacion}-aiplatform.googleapis.com"
    )
    return (
        f"{host}/v1/projects/{proyecto}/locations/{ubicacion}"
        f"/publishers/google/models/{modelo}:generateContent"
    )


def cargar_credenciales(ruta_json: str = "") -> Any:
    """Cuenta de servicio desde `ruta_json`, o credenciales por defecto (ADC) si va vacía."""
    import google.auth
    from google.oauth2 import service_account

    if ruta_json:
        # google-auth no tipa este constructor
        return service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            ruta_json, scopes=[ALCANCE]
        )
    credenciales, _ = google.auth.default(scopes=[ALCANCE])
    return credenciales


def _refrescar(credenciales: Any) -> None:
    from google.auth.transport.requests import Request

    credenciales.refresh(Request())


class ModeloVertex:
    def __init__(
        self,
        proyecto: str,
        modelo: str,
        ubicacion: str = "eu",
        credenciales: Any = None,
        ruta_credenciales: str = "",
        timeout_s: float = 30.0,
        max_tokens: int = 160,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not proyecto:
            raise ValueError("Vertex AI requiere BC_VERTEX_PROYECTO")
        self.modelo = modelo
        self.nombre = f"vertex/{modelo}"
        self.max_tokens = max_tokens
        self._url = url_generacion(proyecto, ubicacion, modelo)
        self._credenciales = credenciales
        self._ruta_credenciales = ruta_credenciales
        self._cerrojo = asyncio.Lock()
        self._cliente = httpx.AsyncClient(timeout=timeout_s, transport=transporte)

    async def _token(self) -> str:
        if self._credenciales is not None and self._credenciales.valid:
            return str(self._credenciales.token)
        async with self._cerrojo:  # un solo refresco aunque lleguen varios turnos a la vez
            if self._credenciales is None:
                self._credenciales = await asyncio.to_thread(
                    cargar_credenciales, self._ruta_credenciales
                )
            if not self._credenciales.valid:
                await asyncio.to_thread(_refrescar, self._credenciales)
            return str(self._credenciales.token)

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:
        if instruccion_formato is None:
            instruccion_formato = describir_esquema(esquema)
        contenido = f"{sistema}\n\n{instruccion_formato}" if instruccion_formato else sistema
        configuracion: dict[str, Any] = {
            "temperature": 0,
            "maxOutputTokens": self.max_tokens,
            "responseMimeType": "application/json",
        }
        if self.modelo.startswith("gemini-2.5"):
            configuracion["thinkingConfig"] = {"thinkingBudget": 0}
        cuerpo = {
            "systemInstruction": {"parts": [{"text": contenido}]},
            "contents": [{"role": "user", "parts": [{"text": usuario}]}],
            "generationConfig": configuracion,
        }
        try:
            token = await self._token()
            r = await self._cliente.post(
                self._url, json=cuerpo, headers={"Authorization": f"Bearer {token}"}
            )
            datos = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Error llamando a Vertex AI: {e}") from e
        except Exception as e:  # noqa: BLE001 - errores de google-auth sin exponer el token
            raise LLMError(f"Error de credenciales de Vertex AI: {type(e).__name__}") from e
        if r.status_code != 200:
            error = datos.get("error", datos) if isinstance(datos, dict) else datos
            raise LLMError(f"Vertex AI error {r.status_code}: {error}")
        try:
            partes = datos["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
        except (KeyError, IndexError, TypeError):
            texto = ""
        if not texto.strip():
            raise LLMError("Vertex AI no devolvió contenido (respuesta vacía o bloqueada)")
        return _validar(_extraer_json(texto), esquema)

    async def disponible(self) -> bool:
        return True

    async def calentar(self) -> None:
        return None
