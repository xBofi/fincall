from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias


@pytest.fixture
async def cliente(deps: Dependencias):
    app = crear_app(deps)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_salud(cliente: AsyncClient) -> None:
    r = await cliente.get("/api/salud")
    assert r.status_code == 200
    assert r.json()["grafos"] == ["estandar"]


async def test_fincas_incluye_viviendas(cliente: AsyncClient) -> None:
    r = await cliente.get("/api/fincas")
    fincas = r.json()
    assert {f["id"] for f in fincas} == {"finca-mayor-12", "finca-alameda-5"}
    assert "3º B" in next(f for f in fincas if f["id"] == "finca-mayor-12")["viviendas"]


async def test_conversacion_por_api(cliente: AsyncClient) -> None:
    r = await cliente.post("/api/llamadas", json={"telefono_llamante": "+34600111222"})
    assert r.status_code == 201
    llamada_id = r.json()["llamada_id"]
    assert r.json()["esperando_usuario"] is True

    for texto in [
        "Calle Mayor 12",
        "sí",
        "4 A",
        "Pedro",
        "No funciona el enchufe de la cocina",
        "sí",
    ]:
        r = await cliente.post(f"/api/llamadas/{llamada_id}/mensajes", json={"texto": texto})
        assert r.status_code == 200
    vista = r.json()
    assert vista["finalizada"] is True
    assert vista["datos"]["categoria"] == "electricidad"
    assert vista["datos"]["incidencia_id"]

    r = await cliente.get("/api/incidencias")
    assert len(r.json()) == 1


async def test_llamada_inexistente_404(cliente: AsyncClient) -> None:
    r = await cliente.post("/api/llamadas/nope/mensajes", json={"texto": "hola"})
    assert r.status_code == 404
