"""Edición del catálogo desde la consola: fincas, viviendas, profesionales, equipos y quién
atiende cada categoría. Lo que se edita aquí es exactamente lo que el asistente consulta."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import SECRETO_DE_PRUEBA, sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.domain.entidades import Incidencia, Llamada
from building_call.graph import plantillas as pl
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import settings as settings_globales

BASE = "/api/catalogos"


@pytest.fixture
async def cliente(deps: Dependencias, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(settings_globales, "sesion_secreto", SECRETO_DE_PRUEBA)
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo, "owner"))  # type: ignore[arg-type]
        c.repo = deps.repo  # type: ignore[attr-defined]
        yield c
    pl.cargar({})


def _como(c: AsyncClient, rol: str) -> AsyncClient:
    otro = AsyncClient(transport=c._transport, base_url="http://localhost")
    otro.cookies.set("bc_sesion", sesion_de_prueba(c.repo, rol))  # type: ignore[attr-defined]
    return otro


NUEVA_FINCA = {
    "administrador_id": "adm-demo",
    "direccion": "Calle del Olmo 7",
    "correo": "olmo7@demo.test",
    "telefono_oficial": "+34910000077",
}


async def _crear_finca(c: AsyncClient, **cambios: str) -> dict:
    r = await c.post(f"{BASE}/fincas", json={**NUEVA_FINCA, **cambios})
    assert r.status_code == 201, r.text
    return r.json()


def _acciones(repo: RepositorioMemoria) -> list[str]:
    return [e["accion"] for e in repo.auditoria]


async def _llamada_con_incidencia(
    repo: RepositorioMemoria,
    finca_id: str = "finca-mayor-12",
    vivienda_id: str = "finca-mayor-12-u-1A",
    especialista_id: str | None = None,
    equipo_id: str | None = None,
) -> None:
    ahora = datetime.now(UTC)
    await repo.guardar_llamada(
        Llamada(id="ll-1", iniciada_en=ahora, finca_id=finca_id, vivienda_id=vivienda_id)
    )
    await repo.guardar_incidencia(
        Incidencia(
            id="inc-1",
            llamada_id="ll-1",
            finca_id=finca_id,
            vivienda_id=vivienda_id,
            descripcion="Fuga en el techo",
            categoria="fontaneria",
            especialista_id=especialista_id,
            urgencia="media",
            creada_en=ahora,
            equipo_id=equipo_id,
        )
    )


# ------------------------------------------------------------------------------------ permisos


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", "/fincas"),
        ("put", "/fincas/finca-mayor-12"),
        ("delete", "/fincas/finca-mayor-12"),
        ("post", "/fincas/finca-mayor-12/viviendas"),
        ("put", "/fincas/finca-mayor-12/viviendas/finca-mayor-12-u-1A"),
        ("delete", "/fincas/finca-mayor-12/viviendas/finca-mayor-12-u-1A"),
        ("post", "/fincas/finca-mayor-12/equipos"),
        ("put", "/fincas/finca-mayor-12/equipos/eq-mayor12-portero"),
        ("delete", "/fincas/finca-mayor-12/equipos/eq-mayor12-portero"),
        ("post", "/especialistas"),
        ("put", "/especialistas/esp-fontanero"),
        ("delete", "/especialistas/esp-fontanero"),
        ("put", "/administradores/adm-demo/categorias/fontaneria"),
    ],
)
async def test_calidad_no_edita_el_catalogo(cliente: AsyncClient, metodo: str, ruta: str) -> None:
    calidad = _como(cliente, "calidad")
    kwargs = {} if metodo == "delete" else {"json": {}}
    r = await getattr(calidad, metodo)(BASE + ruta, **kwargs)
    assert r.status_code == 403
    await calidad.aclose()


async def test_sin_sesion_no_se_edita(cliente: AsyncClient) -> None:
    anonimo = AsyncClient(transport=cliente._transport, base_url="http://localhost")
    assert (await anonimo.post(f"{BASE}/fincas", json=NUEVA_FINCA)).status_code == 401
    await anonimo.aclose()


async def test_el_rol_administrador_si_edita(cliente: AsyncClient) -> None:
    admin = _como(cliente, "administrador")
    r = await admin.post(f"{BASE}/fincas", json=NUEVA_FINCA)
    assert r.status_code == 201
    await admin.aclose()


# -------------------------------------------------------------------------------------- fincas


async def test_crear_finca_genera_id_y_la_ve_el_asistente(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    finca = await _crear_finca(cliente, id="finca-elegida-por-mi")  # type: ignore[arg-type]
    assert finca["id"] != "finca-elegida-por-mi" and finca["id"].startswith("finca-")
    assert finca["direccion"] == "Calle del Olmo 7"
    # El asistente la encuentra en su repositorio y avisa al contacto de la finca
    assert await repo.obtener_finca(finca["id"]) is not None
    cfg, cfg_id = await repo.configuracion_efectiva(finca["id"])
    assert cfg.contacto is not None and cfg.contacto.correo == "olmo7@demo.test"
    assert cfg_id
    assert "finca_creada" in _acciones(repo)


async def test_crear_finca_valida_administrador_y_direccion_repetida(cliente: AsyncClient) -> None:
    r = await cliente.post(f"{BASE}/fincas", json={**NUEVA_FINCA, "administrador_id": "adm-x"})
    assert r.status_code == 422
    r = await cliente.post(f"{BASE}/fincas", json={**NUEVA_FINCA, "direccion": "calle mayor 12"})
    assert r.status_code == 409
    r = await cliente.post(f"{BASE}/fincas", json={**NUEVA_FINCA, "correo": "no-es-correo"})
    assert r.status_code == 422


async def test_editar_finca_cambia_datos_y_crea_version_de_configuracion(
    cliente: AsyncClient,
) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    _, antes = await repo.configuracion_efectiva("finca-alameda-5")
    r = await cliente.put(
        f"{BASE}/fincas/finca-alameda-5",
        json={
            "direccion": "Avenida de la Alameda 5",
            "correo": "nuevo@demo.test",
            "telefono_oficial": "+34910000099",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["correo"] == "nuevo@demo.test"
    cfg, despues = await repo.configuracion_efectiva("finca-alameda-5")
    assert despues != antes
    assert cfg.contacto is not None and cfg.contacto.correo == "nuevo@demo.test"
    # Lo demás de la capa de finca se conserva
    assert cfg.intentos_vivienda == 4
    evento = next(e for e in repo.auditoria if e["accion"] == "finca_editada")
    assert evento["detalle"]["antes"]["correo"] == "alameda5@demo.test"
    assert (await cliente.put(f"{BASE}/fincas/nada", json=NUEVA_FINCA)).status_code == 404


async def test_borrar_finca_sin_actividad(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    finca = await _crear_finca(cliente)
    await cliente.post(f"{BASE}/fincas/{finca['id']}/viviendas", json={"piso": "1", "puerta": "A"})
    r = await cliente.delete(f"{BASE}/fincas/{finca['id']}")
    assert r.status_code == 200
    assert await repo.obtener_finca(finca["id"]) is None
    assert await repo.viviendas_de(finca["id"]) == []
    assert "finca_borrada" in _acciones(repo)
    assert (await cliente.delete(f"{BASE}/fincas/{finca['id']}")).status_code == 404


async def test_no_se_borra_una_finca_con_llamadas_o_incidencias(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    await _llamada_con_incidencia(repo)
    r = await cliente.delete(f"{BASE}/fincas/finca-mayor-12")
    assert r.status_code == 409
    assert "llamada" in r.json()["detail"] and "incidencia" in r.json()["detail"]
    assert await repo.obtener_finca("finca-mayor-12") is not None


# ------------------------------------------------------------------------------------ viviendas


async def test_crear_editar_y_borrar_vivienda(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    r = await cliente.post(
        f"{BASE}/fincas/finca-mayor-12/viviendas",
        json={"id": "a-mano", "piso": "5", "puerta": "a"},
    )
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["id"] != "a-mano" and v["puerta"] == "A" and v["piso"] == "5"
    assert any(x.id == v["id"] for x in await repo.viviendas_de("finca-mayor-12"))

    # Repetida: el asistente no sabría cuál es
    r = await cliente.post(
        f"{BASE}/fincas/finca-mayor-12/viviendas", json={"piso": "5", "puerta": "A"}
    )
    assert r.status_code == 409

    r = await cliente.put(
        f"{BASE}/fincas/finca-mayor-12/viviendas/{v['id']}",
        json={"portal": None, "piso": "5", "puerta": "C"},
    )
    assert r.status_code == 200 and r.json()["puerta"] == "C"

    r = await cliente.delete(f"{BASE}/fincas/finca-mayor-12/viviendas/{v['id']}")
    assert r.status_code == 200
    assert all(x.id != v["id"] for x in await repo.viviendas_de("finca-mayor-12"))
    assert {"vivienda_creada", "vivienda_editada", "vivienda_borrada"} <= set(_acciones(repo))


async def test_vivienda_de_otra_finca_da_404(cliente: AsyncClient) -> None:
    r = await cliente.put(
        f"{BASE}/fincas/finca-alameda-5/viviendas/finca-mayor-12-u-1A",
        json={"piso": "1", "puerta": "A"},
    )
    assert r.status_code == 404
    r = await cliente.post(f"{BASE}/fincas/nada/viviendas", json={"piso": "1", "puerta": "A"})
    assert r.status_code == 404


async def test_no_se_borra_una_vivienda_con_incidencias(cliente: AsyncClient) -> None:
    await _llamada_con_incidencia(cliente.repo)  # type: ignore[attr-defined]
    r = await cliente.delete(f"{BASE}/fincas/finca-mayor-12/viviendas/finca-mayor-12-u-1A")
    assert r.status_code == 409


# ------------------------------------------------------------------------------------- equipos


async def test_crear_editar_y_borrar_equipo(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    r = await cliente.post(
        f"{BASE}/fincas/finca-alameda-5/equipos",
        json={
            "tipo": "grupo de presión",
            "ubicacion": "sótano",
            "marca": "Grundfos",
            "mantenedor_id": "esp-fontanero",
            "garantia_hasta": "2027-05-01",
        },
    )
    assert r.status_code == 201, r.text
    eq = r.json()
    assert eq["id"].startswith("eq-") and eq["descripcion"] == "grupo de presión Grundfos (sótano)"
    assert any(e.id == eq["id"] for e in await repo.equipos_de("finca-alameda-5"))

    r = await cliente.put(
        f"{BASE}/fincas/finca-alameda-5/equipos/{eq['id']}",
        json={"tipo": "grupo de presión", "contrato": "GP-1", "mantenedor_id": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contrato"] == "GP-1" and r.json()["mantenedor_id"] is None

    r = await cliente.delete(f"{BASE}/fincas/finca-alameda-5/equipos/{eq['id']}")
    assert r.status_code == 200
    assert all(e.id != eq["id"] for e in await repo.equipos_de("finca-alameda-5"))
    assert {"equipo_creado", "equipo_editado", "equipo_borrado"} <= set(_acciones(repo))


async def test_el_mantenedor_debe_ser_del_mismo_administrador(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    from building_call.domain.entidades import Administrador, Especialista

    repo.administradores["adm-otro"] = Administrador(id="adm-otro", nombre="Otra Admin")
    repo.especialistas["esp-ajeno"] = Especialista(
        id="esp-ajeno",
        administrador_id="adm-otro",
        nombre="Ajeno",
        categoria_principal="fontaneria",
        canal_contacto="correo",
        destino="ajeno@demo.test",
    )
    for mantenedor in ("esp-ajeno", "esp-inexistente"):
        r = await cliente.post(
            f"{BASE}/fincas/finca-mayor-12/equipos",
            json={"tipo": "ascensor", "mantenedor_id": mantenedor},
        )
        assert r.status_code == 422, mantenedor


async def test_no_se_borra_un_equipo_con_incidencias(cliente: AsyncClient) -> None:
    await _llamada_con_incidencia(cliente.repo, equipo_id="eq-mayor12-portero")  # type: ignore[attr-defined]
    r = await cliente.delete(f"{BASE}/fincas/finca-mayor-12/equipos/eq-mayor12-portero")
    assert r.status_code == 409


# ------------------------------------------------------------------------------- profesionales

NUEVO_ESP = {
    "administrador_id": "adm-demo",
    "nombre": "Pinturas Sol",
    "categoria_principal": "pintura",
    "canal_contacto": "correo",
    "destino": "pinturas@demo.test",
}


async def test_crear_y_editar_profesional(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    r = await cliente.post(f"{BASE}/especialistas", json={**NUEVO_ESP, "id": "mio"})
    assert r.status_code == 201, r.text
    esp = r.json()
    assert esp["id"] != "mio" and esp["id"].startswith("esp-")
    assert any(e.id == esp["id"] for e in await repo.especialistas_de("adm-demo"))

    r = await cliente.put(
        f"{BASE}/especialistas/{esp['id']}",
        json={**NUEVO_ESP, "canal_contacto": "llamada", "destino": "+34600111222"},
    )
    assert r.status_code == 200 and r.json()["canal_contacto"] == "llamada"
    assert {"especialista_creado", "especialista_editado"} <= set(_acciones(repo))


async def test_profesional_valida_categoria_y_destino(cliente: AsyncClient) -> None:
    casos = [
        {"categoria_principal": "astrologia"},
        {"canal_contacto": "correo", "destino": "600111222"},
        {"canal_contacto": "llamada", "destino": "pinturas@demo.test"},
        {"canal_contacto": "fax"},
        {"administrador_id": "adm-x"},
    ]
    for cambio in casos:
        r = await cliente.post(f"{BASE}/especialistas", json={**NUEVO_ESP, **cambio})
        assert r.status_code == 422, cambio


async def test_borrar_profesional_libre(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    esp = (await cliente.post(f"{BASE}/especialistas", json=NUEVO_ESP)).json()
    r = await cliente.delete(f"{BASE}/especialistas/{esp['id']}")
    assert r.status_code == 200
    assert all(e.id != esp["id"] for e in await repo.especialistas_de("adm-demo"))
    assert "especialista_borrado" in _acciones(repo)


async def test_no_se_borra_en_silencio_un_profesional_asignado(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    # Asignado a una categoría
    r = await cliente.delete(f"{BASE}/especialistas/esp-fontanero")
    assert r.status_code == 409
    assert "Fontanería" in r.json()["detail"] or "fontaneria" in r.json()["detail"]
    # Mantiene un equipo
    r = await cliente.delete(f"{BASE}/especialistas/esp-porteros")
    assert r.status_code == 409 and "portero" in r.json()["detail"]
    assert any(e.id == "esp-porteros" for e in await repo.especialistas_de("adm-demo"))


async def test_forzar_desasigna_y_lo_dice(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    r = await cliente.delete(f"{BASE}/especialistas/esp-ascensores", params={"forzar": "true"})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["categorias_desasignadas"] == ["ascensor"]
    assert cuerpo["equipos_sin_mantenedor"] == ["eq-mayor12-ascensor"]
    cfg, _ = await repo.configuracion_efectiva("finca-mayor-12")
    assert "ascensor" not in cfg.especialista_por_categoria
    ascensor = next(e for e in await repo.equipos_de("finca-mayor-12") if e.tipo == "ascensor")
    assert ascensor.mantenedor_id is None


async def test_no_se_borra_un_profesional_con_incidencias(cliente: AsyncClient) -> None:
    await _llamada_con_incidencia(cliente.repo, especialista_id="esp-cerrajero")  # type: ignore[attr-defined]
    r = await cliente.delete(f"{BASE}/especialistas/esp-cerrajero", params={"forzar": "true"})
    assert r.status_code == 409


# ------------------------------------------------------------------- quién atiende cada categoría


async def test_asignar_especialista_crea_version_nueva(cliente: AsyncClient) -> None:
    repo: RepositorioMemoria = cliente.repo  # type: ignore[attr-defined]
    esp = (await cliente.post(f"{BASE}/especialistas", json=NUEVO_ESP)).json()
    antes = repo.config_administrador["adm-demo"]
    versiones = len(repo.historial_configuracion)

    r = await cliente.put(
        f"{BASE}/administradores/adm-demo/categorias/pintura", json={"especialista_id": esp["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["especialista_por_categoria"]["pintura"] == esp["id"]
    # Versión nueva, la anterior intacta y en el historial
    assert len(repo.historial_configuracion) == versiones + 1
    assert antes.especialista_por_categoria and "pintura" not in antes.especialista_por_categoria
    assert repo.config_administrador["adm-demo"].pedir_detalle is False  # el resto se hereda
    cfg, _ = await repo.configuracion_efectiva("finca-mayor-12")
    assert cfg.especialista_por_categoria["pintura"] == esp["id"]
    evento = next(e for e in repo.auditoria if e["accion"] == "categoria_asignada")
    assert evento["detalle"] == {
        "categoria": "pintura",
        "antes": None,
        "despues": esp["id"],
        "configuracion_id": r.json()["configuracion_id"],
    }

    # Quitar la asignación: queda para el administrador
    r = await cliente.put(
        f"{BASE}/administradores/adm-demo/categorias/pintura", json={"especialista_id": None}
    )
    assert r.status_code == 200 and "pintura" not in r.json()["especialista_por_categoria"]


async def test_asignar_valida_categoria_y_profesional(cliente: AsyncClient) -> None:
    ruta = f"{BASE}/administradores/adm-demo/categorias"
    r = await cliente.put(f"{ruta}/astrologia", json={"especialista_id": "esp-fontanero"})
    assert r.status_code == 422
    r = await cliente.put(f"{ruta}/pintura", json={"especialista_id": "esp-inexistente"})
    assert r.status_code == 422
    r = await cliente.put(
        f"{BASE}/administradores/adm-x/categorias/pintura", json={"especialista_id": None}
    )
    assert r.status_code == 404


async def test_los_administradores_traen_su_asignacion(cliente: AsyncClient) -> None:
    admins = (await cliente.get(f"{BASE}/administradores")).json()
    demo = next(a for a in admins if a["id"] == "adm-demo")
    assert demo["especialista_por_categoria"]["fontaneria"] == "esp-fontanero"
    assert "pintura" in demo["catalogo_categorias"]
