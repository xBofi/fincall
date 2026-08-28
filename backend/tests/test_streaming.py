"""WebSocket de voz en streaming con una sesión STT falsa, y carrera de modelos."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias
from building_call.llm.carrera import ModeloCarrera
from building_call.llm.ollama import LLMError
from building_call.stt_streaming import (
    AssemblyAIConfigError,
    EventoTurno,
    SesionStreamingAssemblyAI,
    validar_endpoint_eu,
)


class SesionFalsa:
    """Emite un 'final' cada vez que recibe la orden fin_turno; el texto sale de una cola."""

    textos = ["Calle Mayor 12", "sí"]

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.audio = bytearray()
        self.conexion_ms = 7
        self._cola: asyncio.Queue[EventoTurno | None] = asyncio.Queue()
        self.cerrada = False
        self._i = 0

    async def abrir(self) -> None:
        return None

    async def enviar_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def forzar_fin_turno(self) -> None:
        await self._cola.put(EventoTurno("parcial", "calle ma"))
        await self._cola.put(EventoTurno("final", self.textos[self._i % len(self.textos)]))
        self._i += 1

    async def eventos(self):  # type: ignore[no-untyped-def]
        while True:
            ev = await self._cola.get()
            if ev is None:
                return
            yield ev

    async def cerrar(self) -> None:
        self.cerrada = True
        await self._cola.put(None)


@pytest.fixture
def cliente_ws(deps: Dependencias) -> TestClient:
    return TestClient(crear_app(deps, fabrica_streaming=SesionFalsa))


def test_turno_por_streaming(cliente_ws: TestClient) -> None:
    llamada_id = cliente_ws.post("/api/llamadas", json={}).json()["llamada_id"]
    with cliente_ws.websocket_connect(f"/api/llamadas/{llamada_id}/voz?sample_rate=16000") as ws:
        assert ws.receive_json()["type"] == "listo"
        ws.send_bytes(b"\x00" * 3200)
        ws.send_json({"type": "fin_turno"})
        assert ws.receive_json() == {"type": "parcial", "texto": "calle ma"}
        assert ws.receive_json() == {"type": "final", "texto": "Calle Mayor 12"}
        respuesta = ws.receive_json()
        assert respuesta["type"] == "respuesta"
        assert respuesta["nodo_actual"] == "identificar_finca"
        assert "Calle Mayor 12" in respuesta["mensajes_bot"][0]
        assert "stt_eou_a_respuesta_ms" in respuesta["tiempos"]
        ws.send_json({"type": "pausa"})
        ws.send_json({"type": "fin_turno"})
        ws.receive_json()  # parcial
        ws.receive_json()  # final "sí"
        respuesta = ws.receive_json()
        assert respuesta["datos"]["finca_id"] == "finca-mayor-12"


def test_streaming_sin_configurar_devuelve_error(deps: Dependencias) -> None:
    cliente = TestClient(crear_app(deps, fabrica_streaming=None))
    llamada_id = cliente.post("/api/llamadas", json={}).json()["llamada_id"]
    with cliente.websocket_connect(f"/api/llamadas/{llamada_id}/voz") as ws:
        assert ws.receive_json()["type"] == "error"
    assert cliente.get("/api/salud").json()["stt_streaming"] is None


def test_solo_endpoints_europeos() -> None:
    assert validar_endpoint_eu("https://api.eu.assemblyai.com/") == "https://api.eu.assemblyai.com"
    with pytest.raises(AssemblyAIConfigError):
        validar_endpoint_eu("https://api.assemblyai.com")
    with pytest.raises(AssemblyAIConfigError):
        SesionStreamingAssemblyAI("clave", host="streaming.assemblyai.com")
    with pytest.raises(AssemblyAIConfigError):
        SesionStreamingAssemblyAI("")


class ModeloLento:
    def __init__(self, nombre: str, retardo: float, resultado: dict[str, Any] | None) -> None:
        self.nombre = nombre
        self.retardo = retardo
        self.resultado = resultado

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:  # noqa: E501
        await asyncio.sleep(self.retardo)
        if self.resultado is None:
            raise LLMError("falló")
        return self.resultado


async def test_carrera_devuelve_el_primero_valido() -> None:
    carrera = ModeloCarrera(
        ModeloLento("lento", 0.2, {"a": 1}),
        ModeloLento("rapido", 0.01, {"a": 2}),
        ModeloLento("roto", 0.0, None),
    )
    assert await carrera.extraer("s", "u", {}) == {"a": 2}
    assert carrera.ganadores["rapido"] == 1


async def test_carrera_falla_si_todos_fallan() -> None:
    carrera = ModeloCarrera(ModeloLento("a", 0.0, None), ModeloLento("b", 0.0, None))
    with pytest.raises(LLMError):
        await carrera.extraer("s", "u", {})
