from __future__ import annotations

import pytest
from conftest import sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias
from building_call.stt import Transcripcion


class STTFalso:
    nombre = "falso/stt"

    def __init__(self, texto: str = "Calle Mayor 12") -> None:
        self.texto = texto

    def disponible(self) -> bool:
        return True

    async def transcribir(self, audio: bytes, mime: str) -> Transcripcion:
        return Transcripcion(
            texto=self.texto, ms=42, modelo=self.nombre, detalle={"bytes": len(audio)}
        )


@pytest.fixture
async def cliente(deps: Dependencias):
    app = crear_app(deps, stt=STTFalso())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
        yield c


async def test_salud(cliente: AsyncClient) -> None:
    r = await cliente.get("/api/salud")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["grafos"] == ["estandar"]
    assert cuerpo["stt_disponible"] is True
    assert cuerpo["umbrales_ms"]["objetivo"] == 1200


async def test_fincas_incluye_viviendas(cliente: AsyncClient) -> None:
    r = await cliente.get("/api/fincas")
    fincas = r.json()
    assert {f["id"] for f in fincas} == {"finca-mayor-12", "finca-alameda-5"}
    assert "3º B" in next(f for f in fincas if f["id"] == "finca-mayor-12")["viviendas"]


async def test_conversacion_por_api(cliente: AsyncClient, repo: object) -> None:
    r = await cliente.post("/api/llamadas", json={"telefono_llamante": "+34600111222"})
    assert r.status_code == 201
    llamada_id = r.json()["llamada_id"]
    assert r.json()["esperando_usuario"] is True
    assert r.json()["turno"] == 0

    for texto in [
        "Calle Mayor 12",
        "sí",
        "4 A",
        "No funciona el enchufe de la cocina",
        "sí",
    ]:
        r = await cliente.post(f"/api/llamadas/{llamada_id}/mensajes", json={"texto": texto})
        assert r.status_code == 200
    vista = r.json()
    assert vista["finalizada"] is True
    assert vista["turno"] == 5
    assert vista["datos"]["categoria"] == "electricidad"
    assert vista["datos"]["incidencia_id"]
    assert "servidor_ms" in vista["tiempos"]

    r = await cliente.get("/api/incidencias")
    assert len(r.json()) == 1
    llamada = repo.llamadas[llamada_id]  # type: ignore[attr-defined]
    assert llamada.estado == "finalizada"
    assert llamada.finalizada_en is not None
    assert llamada.requiere_revision is False


async def test_turno_por_audio_usa_stt_del_servidor(cliente: AsyncClient) -> None:
    llamada_id = (await cliente.post("/api/llamadas", json={})).json()["llamada_id"]
    r = await cliente.post(
        f"/api/llamadas/{llamada_id}/audio",
        files={"fichero": ("turno.webm", b"\x1a\x45\xdf\xa3 audio falso", "audio/webm")},
        data={"mime": "audio/webm"},
    )
    assert r.status_code == 200
    vista = r.json()
    assert vista["transcripcion"] == "Calle Mayor 12"
    assert vista["tiempos"]["stt_ms"] == 42
    assert vista["datos"]["intentos_finca"] == 0
    assert "Calle Mayor 12" in vista["mensajes_bot"][0]


async def test_metricas_cliente_y_agregados(cliente: AsyncClient) -> None:
    llamada_id = (await cliente.post("/api/llamadas", json={})).json()["llamada_id"]
    await cliente.post(f"/api/llamadas/{llamada_id}/mensajes", json={"texto": "Calle Mayor 12"})
    r = await cliente.post(
        f"/api/llamadas/{llamada_id}/metricas",
        json={"turno": 1, "respuesta_ms": 950, "vad_silencio_ms": 550, "tts_inicio_ms": 80},
    )
    assert r.status_code == 200
    assert r.json()["calificacion"] == "objetivo"
    r = await cliente.get(f"/api/llamadas/{llamada_id}/metricas")
    agregados = r.json()["agregados"]
    assert agregados["turnos"] == 2  # saludo + primer turno
    assert agregados["respuesta_ms"]["p50"] == 950
    r = await cliente.get("/api/metricas")
    assert r.json()["agregados"]["calificaciones"]["objetivo"] == 1
    r = await cliente.post(
        f"/api/llamadas/{llamada_id}/metricas", json={"turno": 99, "respuesta_ms": 1}
    )
    assert r.status_code == 404


async def test_llamada_inexistente_404(cliente: AsyncClient) -> None:
    r = await cliente.post("/api/llamadas/nope/mensajes", json={"texto": "hola"})
    assert r.status_code == 404
