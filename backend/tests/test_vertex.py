"""Cliente Vertex AI: solo Europa, JSON validado y token reutilizado mientras sea válido."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from building_call.llm.ollama import LLMError
from building_call.llm.vertex import ModeloVertex, url_generacion, validar_ubicacion

ESQUEMA = {
    "type": "object",
    "properties": {"intencion": {"type": "string"}, "urgente": {"type": "boolean"}},
}


class CredencialesFalsas:
    def __init__(self) -> None:
        self.refrescos = 0
        self.token: str | None = None

    @property
    def valid(self) -> bool:
        return self.token is not None

    def refresh(self, _request: Any) -> None:
        self.refrescos += 1
        self.token = f"token-{self.refrescos}"


def _respuesta(texto: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": texto}]}}]}


def _modelo(
    manejador: Any, credenciales: Any, modelo: str = "gemini-2.5-flash-lite"
) -> ModeloVertex:
    return ModeloVertex(
        "mi-proyecto",
        modelo,
        credenciales=credenciales,
        transporte=httpx.MockTransport(manejador),
    )


def test_url_multi_region_ue() -> None:
    assert url_generacion("p", "eu", "gemini-2.5-flash-lite") == (
        "https://aiplatform.eu.rep.googleapis.com/v1/projects/p/locations/eu"
        "/publishers/google/models/gemini-2.5-flash-lite:generateContent"
    )


def test_url_region_europea() -> None:
    assert url_generacion("p", "europe-southwest1", "m").startswith(
        "https://europe-southwest1-aiplatform.googleapis.com/v1/projects/p/locations/europe-southwest1/"
    )


@pytest.mark.parametrize("ubicacion", ["us-central1", "global", "us", "asia-northeast1", ""])
def test_rechaza_ubicacion_no_europea(ubicacion: str) -> None:
    with pytest.raises(ValueError, match="no europea"):
        validar_ubicacion(ubicacion)
    with pytest.raises(ValueError):
        ModeloVertex("p", "m", ubicacion=ubicacion, credenciales=CredencialesFalsas())


async def test_extrae_json_y_desactiva_razonamiento(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refrescar", lambda c: c.refresh(None))
    peticiones: list[httpx.Request] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return httpx.Response(200, json=_respuesta('{"intencion": "averia", "urgente": true}'))

    llm = _modelo(manejador, CredencialesFalsas())
    assert await llm.extraer("sistema", "Hay una fuga", ESQUEMA) == {
        "intencion": "averia",
        "urgente": True,
    }
    cuerpo = json.loads(peticiones[0].content)
    assert cuerpo["generationConfig"]["responseMimeType"] == "application/json"
    assert cuerpo["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert cuerpo["contents"][0]["parts"][0]["text"] == "Hay una fuga"
    assert peticiones[0].headers["Authorization"] == "Bearer token-1"
    assert peticiones[0].url.host == "aiplatform.eu.rep.googleapis.com"


async def test_token_se_reutiliza_mientras_es_valido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refrescar", lambda c: c.refresh(None))
    credenciales = CredencialesFalsas()

    def manejador(_peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_respuesta('{"intencion": "x"}'))

    llm = _modelo(manejador, credenciales)
    for _ in range(3):
        await llm.extraer("s", "u", ESQUEMA)
    assert credenciales.refrescos == 1

    credenciales.token = None  # caduca
    await llm.extraer("s", "u", ESQUEMA)
    assert credenciales.refrescos == 2


async def test_error_http_y_respuesta_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refrescar", lambda c: c.refresh(None))
    respuestas = iter(
        [
            httpx.Response(429, json={"error": {"code": 429, "message": "Resource exhausted"}}),
            httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}),
        ]
    )
    llm = _modelo(lambda _p: next(respuestas), CredencialesFalsas())
    with pytest.raises(LLMError, match="429"):
        await llm.extraer("s", "u", ESQUEMA)
    with pytest.raises(LLMError, match="no devolvió contenido"):
        await llm.extraer("s", "u", ESQUEMA)


async def test_gemini_3_no_envia_thinking_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("building_call.llm.vertex._refrescar", lambda c: c.refresh(None))
    cuerpos: list[dict[str, Any]] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        cuerpos.append(json.loads(peticion.content))
        return httpx.Response(200, json=_respuesta('{"intencion": "x"}'))

    await _modelo(manejador, CredencialesFalsas(), "gemini-3.1-flash-lite").extraer(
        "s", "u", ESQUEMA
    )
    assert "thinkingConfig" not in cuerpos[0]["generationConfig"]
