"""API del flujo: grafo, plantillas editables, prompts de solo lectura y catálogos."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SECRETO_DE_PRUEBA, sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph import plantillas as pl
from building_call.graph.manifiesto import MANIFIESTO
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import settings as settings_globales


@pytest.fixture
async def cliente(deps: Dependencias, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(settings_globales, "sesion_secreto", SECRETO_DE_PRUEBA)
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        c.repo = deps.repo  # type: ignore[attr-defined]
        yield c
    pl.cargar({})


async def _owner(c: AsyncClient) -> None:
    c.cookies.set("bc_sesion", sesion_de_prueba(c.repo, "owner"))  # type: ignore[attr-defined]


async def _como(c: AsyncClient, rol: str) -> AsyncClient:
    """Devuelve un cliente ya dentro con ese rol."""
    otro = AsyncClient(transport=c._transport, base_url="http://localhost")
    otro.cookies.set("bc_sesion", sesion_de_prueba(c.repo, rol))  # type: ignore[attr-defined]
    return otro


# ------------------------------------------------------------------------------------ el grafo


async def test_el_grafo_describe_todos_los_nodos_y_sus_uniones(cliente: AsyncClient) -> None:
    await _owner(cliente)
    g = (await cliente.get("/api/grafo")).json()
    assert {n["id"] for n in g["nodos"]} == set(MANIFIESTO)
    assert g["entrada"] == "siguiente_paso"
    # Toda arista sale y llega a algo conocido, y explica por qué
    ids = set(MANIFIESTO) | {"fin"}
    for a in g["aristas"]:
        assert a["origen"] in ids and a["destino"] in ids
        assert a["condicion"]
        assert a["veces"] >= 0


async def test_el_grafo_dice_que_nodos_no_pasan_por_el_modelo(cliente: AsyncClient) -> None:
    """Sin esto, alguien editaría un texto esperando cambiar cómo se entiende un 'sí'."""
    await _owner(cliente)
    nodos = {n["id"]: n for n in (await cliente.get("/api/grafo")).json()["nodos"]}
    assert nodos["siguiente_paso"]["usa_llm"] is False
    assert nodos["identificar_finca"]["usa_llm"] is True
    assert any("sí / no" in c for c in nodos["identificar_finca"]["caminos_deterministas"])
    assert nodos["crear_ticket"]["efectos"]


async def test_el_grafo_no_declara_violaciones_tras_la_suite(cliente: AsyncClient) -> None:
    await _owner(cliente)
    assert (await cliente.get("/api/grafo")).json()["violaciones"] == []


# ------------------------------------------------------------------------------- los prompts


async def test_los_prompts_se_ven_pero_no_se_editan(cliente: AsyncClient) -> None:
    await _owner(cliente)
    r = (await cliente.get("/api/prompts")).json()
    assert r["editables"] is False
    assert {p["id"] for p in r["prompts"]} == {"interpretacion", "clasificacion", "redaccion"}
    for p in r["prompts"]:
        assert p["texto"] and p["version"] and p["contrato"]
    # No existe forma de publicarlos
    assert (await cliente.put("/api/prompts/interpretacion", json={"texto": "x"})).status_code in (
        404,
        405,
    )


# ------------------------------------------------------------------------------- plantillas


async def test_listar_plantillas_dice_cuales_estan_tocadas(cliente: AsyncClient) -> None:
    await _owner(cliente)
    filas = (await cliente.get("/api/plantillas")).json()
    assert {f["id"] for f in filas} == set(pl.CATALOGO)
    assert all(f["sobrescrita"] is False and f["version"] == 0 for f in filas)


async def test_publicar_cambia_lo_que_dice_el_bot_y_queda_auditado(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _owner(cliente)
    r = await cliente.put(
        "/api/plantillas/identificar_finca.candidata",
        json={"texto": "¿Me confirma que su finca es {direccion}?"},
    )
    assert r.status_code == 200
    assert r.json()["sobrescrita"] is True and r.json()["version"] == 1
    assert (
        pl.render("identificar_finca.candidata", direccion="Calle Mayor 12")
        == "¿Me confirma que su finca es Calle Mayor 12?"
    )
    auditoria = await repo.listar_auditoria()
    assert any(e["accion"] == "plantilla_publicada" for e in auditoria)


async def test_publicar_algo_invalido_devuelve_los_problemas(cliente: AsyncClient) -> None:
    await _owner(cliente)
    r = await cliente.put(
        "/api/plantillas/identificar_finca.candidata", json={"texto": "He identificado la finca."}
    )
    assert r.status_code == 422
    problemas = r.json()["detail"]["problemas"]
    assert any("Faltan variables" in p for p in problemas)
    # Y no ha llegado a cambiar nada
    assert not pl.esta_sobrescrita("identificar_finca.candidata")


async def test_restaurar_vuelve_al_texto_del_codigo(cliente: AsyncClient) -> None:
    await _owner(cliente)
    await cliente.put("/api/plantillas/global.relleno", json={"texto": "Sin prisa, le espero."})
    assert pl.esta_sobrescrita("global.relleno")
    r = await cliente.delete("/api/plantillas/global.relleno")
    assert r.status_code == 200 and r.json()["sobrescrita"] is False
    assert pl.texto_efectivo("global.relleno") == pl.CATALOGO["global.relleno"].texto


async def test_el_historial_conserva_lo_publicado(cliente: AsyncClient) -> None:
    await _owner(cliente)
    await cliente.put("/api/plantillas/global.relleno", json={"texto": "Primera versión."})
    await cliente.put("/api/plantillas/global.relleno", json={"texto": "Segunda versión."})
    detalle = (await cliente.get("/api/plantillas/global.relleno")).json()
    assert [h["version"] for h in detalle["historial"]] == [2, 1]
    assert detalle["texto_original"] == pl.CATALOGO["global.relleno"].texto


async def test_validar_previsualiza_sin_publicar(cliente: AsyncClient) -> None:
    await _owner(cliente)
    r = await cliente.post(
        "/api/plantillas/identificar_finca.candidata/validar",
        json={"texto": "¿Me confirma {direccion}?"},
    )
    assert r.status_code == 200
    assert r.json()["problemas"] == []
    assert not pl.esta_sobrescrita("identificar_finca.candidata")


async def test_una_plantilla_que_no_existe_da_404(cliente: AsyncClient) -> None:
    await _owner(cliente)
    assert (await cliente.get("/api/plantillas/inventada.total")).status_code == 404
    assert (
        await cliente.put("/api/plantillas/inventada.total", json={"texto": "hola"})
    ).status_code == 404


# ------------------------------------------------------------------------------- permisos


async def test_solo_el_owner_edita_plantillas(cliente: AsyncClient) -> None:
    await _owner(cliente)
    calidad = await _como(cliente, "calidad")
    # Ver, sí
    assert (await calidad.get("/api/plantillas")).status_code == 200
    assert (await calidad.get("/api/grafo")).status_code == 200
    # Tocar, no
    assert (
        await calidad.put("/api/plantillas/global.relleno", json={"texto": "Otra cosa."})
    ).status_code == 403
    assert (await calidad.delete("/api/plantillas/global.relleno")).status_code == 403
    assert not pl.esta_sobrescrita("global.relleno")
    await calidad.aclose()


async def test_sin_sesion_no_se_ve_nada(cliente: AsyncClient) -> None:
    for ruta in ("/api/grafo", "/api/prompts", "/api/plantillas", "/api/catalogos/fincas"):
        assert (await cliente.get(ruta)).status_code == 401, ruta


# ------------------------------------------------------------------------------- catálogos


async def test_los_catalogos_muestran_fincas_y_profesionales(cliente: AsyncClient) -> None:
    await _owner(cliente)
    fincas = (await cliente.get("/api/catalogos/fincas")).json()
    assert fincas and all(f["direccion"] and f["administrador"] for f in fincas)

    detalle = (await cliente.get(f"/api/catalogos/fincas/{fincas[0]['id']}")).json()
    assert detalle["viviendas"]
    # Las tres capas ya resueltas, para entender por qué esta finca se comporta así
    assert detalle["configuracion_efectiva"]["catalogo_categorias"]

    admins = (await cliente.get("/api/catalogos/administradores")).json()
    assert admins and admins[0]["fincas"]

    espec = (await cliente.get("/api/catalogos/especialistas")).json()
    assert all(e["categoria_principal"] and e["administrador"] for e in espec)


async def test_una_finca_inexistente_da_404(cliente: AsyncClient) -> None:
    await _owner(cliente)
    assert (await cliente.get("/api/catalogos/fincas/finca-fantasma")).status_code == 404


# ------------------------------------------------------------------------------- auditoría


async def test_la_traza_dice_de_que_plantilla_y_version_salio_cada_frase(
    cliente: AsyncClient,
) -> None:
    """Con textos editables, guardar solo la frase no permite saber qué se dijo ni con qué versión."""
    await _owner(cliente)
    llamada = (await cliente.post("/api/llamadas", json={})).json()
    trazas = [t for t in llamada["traza"] if t["accion"] == "plantilla"]
    assert trazas, "el saludo inicial no dejó constancia de su plantilla"
    assert trazas[0]["detalle"] == "identificar_finca.saludo v0"

    # Tras editar, la versión que queda registrada es la nueva
    await cliente.put(
        "/api/plantillas/identificar_finca.saludo",
        json={"texto": "Buenas, soy el asistente de su comunidad. ¿En qué finca está?"},
    )
    otra = (await cliente.post("/api/llamadas", json={})).json()
    trazas = [t for t in otra["traza"] if t["accion"] == "plantilla"]
    assert trazas[0]["detalle"] == "identificar_finca.saludo v1"
    assert "Buenas, soy el asistente" in otra["mensajes_bot"][0]


async def test_la_ficha_de_la_finca_lista_sus_equipos(cliente: AsyncClient) -> None:
    await _owner(cliente)
    finca = (await cliente.get("/api/catalogos/fincas/finca-mayor-12")).json()
    portero = next(e for e in finca["equipos"] if e["id"] == "eq-mayor12-portero")
    assert portero["mantenedor_id"] == "esp-porteros" and portero["contrato"] == "PL-2024-118"
    assert portero["descripcion"] == "portero automático Fermax Citymax (portal)"
