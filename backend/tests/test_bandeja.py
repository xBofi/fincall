"""Bandeja del administrador: incidencias que esperan una decisión suya y sus acciones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import SECRETO_DE_PRUEBA, sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.domain.entidades import Incidencia, Llamada
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import settings as settings_globales

AHORA = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
async def cliente(deps: Dependencias, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(settings_globales, "sesion_secreto", SECRETO_DE_PRUEBA)
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _sembrar(repo: RepositorioMemoria) -> None:
    """Tres incidencias: una del administrador (siniestro del portero), una asignada al
    fontanero y una pendiente sin especialista en otra finca."""
    await repo.guardar_llamada(
        Llamada(
            id="llam-portero",
            iniciada_en=AHORA,
            finca_id="finca-mayor-12",
            vivienda_id="finca-mayor-12-u-3B",
            nombre_declarado="Lucía",
            transcripcion=[
                {"rol": "bot", "texto": "Hola, ¿en qué puedo ayudarle?"},
                {"rol": "vecino", "texto": "Han reventado el portero automático"},
            ],
            resumen="Han vandalizado el portero automático del portal.",
            resultado="incidencia_creada",
            motivo_revision="actuacion:siniestro",
            estado="finalizada",
        )
    )
    await repo.guardar_incidencia(
        Incidencia(
            id="inc-portero",
            llamada_id="llam-portero",
            finca_id="finca-mayor-12",
            vivienda_id="finca-mayor-12-u-3B",
            descripcion="Han reventado el portero automático",
            categoria="electricidad",
            especialista_id=None,
            urgencia="media",
            creada_en=AHORA - timedelta(hours=2),
            equipo_id="eq-mayor12-portero",
            actuacion="siniestro",
            sugerencia="Puede ser un siniestro para el seguro. Equipo: portero automático.",
        )
    )
    await repo.guardar_llamada(
        Llamada(id="llam-grifo", iniciada_en=AHORA, finca_id="finca-mayor-12", estado="finalizada")
    )
    await repo.guardar_incidencia(
        Incidencia(
            id="inc-grifo",
            llamada_id="llam-grifo",
            finca_id="finca-mayor-12",
            vivienda_id="finca-mayor-12-u-1A",
            descripcion="Gotea un grifo del cuarto de contadores",
            categoria="fontaneria",
            especialista_id="esp-fontanero",
            urgencia="alta",
            estado="asignada",
            creada_en=AHORA - timedelta(hours=1),
        )
    )
    await repo.guardar_llamada(
        Llamada(
            id="llam-limpieza",
            iniciada_en=AHORA,
            finca_id="finca-alameda-5",
            motivo_revision="sin_especialista_para_categoria",
            estado="finalizada",
        )
    )
    await repo.guardar_incidencia(
        Incidencia(
            id="inc-limpieza",
            llamada_id="llam-limpieza",
            finca_id="finca-alameda-5",
            vivienda_id="finca-alameda-5-1-1A",
            descripcion="Hay basura en el portal",
            categoria="limpieza",
            especialista_id=None,
            urgencia="emergencia",
            creada_en=AHORA,
        )
    )


async def _entrar(cliente: AsyncClient, repo: RepositorioMemoria, rol: str) -> None:
    await _sembrar(repo)
    cliente.cookies.set("bc_sesion", sesion_de_prueba(repo, rol))


# ------------------------------------------------------------------------------------- permisos


async def test_sin_sesion_la_bandeja_responde_401(cliente: AsyncClient) -> None:
    assert (await cliente.get("/api/bandeja/incidencias")).status_code == 401
    assert (await cliente.get("/api/bandeja/incidencias/inc-portero")).status_code == 401
    r = await cliente.post("/api/bandeja/incidencias/inc-portero/nota", json={"texto": "x"})
    assert r.status_code == 401


async def test_calidad_solo_lee(cliente: AsyncClient, repo: RepositorioMemoria) -> None:
    await _entrar(cliente, repo, "calidad")
    assert (await cliente.get("/api/bandeja/incidencias")).status_code == 200
    assert (await cliente.get("/api/bandeja/incidencias/inc-portero")).status_code == 200
    base = "/api/bandeja/incidencias/inc-portero"
    assert (
        await cliente.post(f"{base}/asignar", json={"especialista_id": "esp-porteros"})
    ).status_code == 403
    assert (await cliente.post(f"{base}/estado", json={"estado": "en_curso"})).status_code == 403
    assert (await cliente.post(f"{base}/nota", json={"texto": "Visto"})).status_code == 403
    assert repo.incidencias["inc-portero"].estado == "pendiente"
    assert await repo.listar_historial_incidencia("inc-portero") == []


@pytest.mark.parametrize("rol", ["owner", "administrador"])
async def test_owner_y_administrador_actuan(
    cliente: AsyncClient, repo: RepositorioMemoria, rol: str
) -> None:
    await _entrar(cliente, repo, rol)
    r = await cliente.post("/api/bandeja/incidencias/inc-portero/nota", json={"texto": "Visto"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------------------- lista


async def test_lista_pone_primero_lo_pendiente_del_administrador(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _entrar(cliente, repo, "administrador")
    filas = (await cliente.get("/api/bandeja/incidencias")).json()
    ids = [f["id"] for f in filas]
    # Pendientes del administrador primero (por urgencia), después el resto
    assert ids == ["inc-limpieza", "inc-portero", "inc-grifo"]
    portero = filas[1]
    assert portero["pendiente_administrador"] is True
    assert portero["finca_direccion"] == "Calle Mayor 12"
    assert portero["vivienda"] == "3º B"
    assert portero["motivo_revision"] == "actuacion:siniestro"
    assert portero["sugerencia"].startswith("Puede ser un siniestro")
    assert filas[2]["especialista_nombre"] == "Fontanería Pérez"
    assert filas[2]["pendiente_administrador"] is False


async def test_lista_filtra(cliente: AsyncClient, repo: RepositorioMemoria) -> None:
    await _entrar(cliente, repo, "owner")

    async def ids(query: str) -> list[str]:
        return [f["id"] for f in (await cliente.get(f"/api/bandeja/incidencias?{query}")).json()]

    assert await ids("pendientes_administrador=true") == ["inc-limpieza", "inc-portero"]
    assert await ids("estado=asignada") == ["inc-grifo"]
    assert await ids("finca_id=finca-alameda-5") == ["inc-limpieza"]
    assert await ids("urgencia=alta") == ["inc-grifo"]
    assert await ids("finca_id=finca-mayor-12&pendientes_administrador=true") == ["inc-portero"]
    assert (await cliente.get("/api/bandeja/incidencias?estado=inventado")).status_code == 422


# -------------------------------------------------------------------------------------- detalle


async def test_detalle_trae_llamada_equipo_y_especialistas(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _entrar(cliente, repo, "calidad")
    d = (await cliente.get("/api/bandeja/incidencias/inc-portero")).json()
    assert d["incidencia"]["sugerencia"].startswith("Puede ser un siniestro")
    assert d["llamada"]["resumen"].startswith("Han vandalizado")
    assert d["llamada"]["motivo_revision"] == "actuacion:siniestro"
    assert d["llamada"]["transcripcion"][1]["rol"] == "vecino"
    assert d["equipo"]["descripcion"] == "portero automático Fermax Citymax (portal)"
    assert d["equipo"]["mantenedor"]["nombre"] == "Porteros Levante"
    assert d["finca"]["direccion"] == "Calle Mayor 12"
    # Para el selector de asignación: solo los especialistas de su administrador
    assert {e["id"] for e in d["especialistas"]} >= {"esp-porteros", "esp-fontanero"}
    assert d["historial"] == []
    assert any(
        a["accion"] == "ver_incidencia" and a["recurso"] == "incidencia:inc-portero"
        for a in await repo.listar_auditoria()
    )
    assert (await cliente.get("/api/bandeja/incidencias/inc-nada")).status_code == 404


# -------------------------------------------------------------------------------------- acciones


async def test_asignar_deja_el_aviso_pendiente_de_envio(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _entrar(cliente, repo, "administrador")
    r = await cliente.post(
        "/api/bandeja/incidencias/inc-portero/asignar",
        json={"especialista_id": "esp-porteros", "nota": "Que lo mire el mantenedor"},
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["incidencia"]["estado"] == "asignada"
    assert cuerpo["incidencia"]["especialista_id"] == "esp-porteros"
    assert cuerpo["aviso"] == "pendiente_envio"
    assert repo.incidencias["inc-portero"].especialista_id == "esp-porteros"

    historial = await repo.listar_historial_incidencia("inc-portero")
    assert len(historial) == 1
    h = historial[0]
    assert h.accion == "asignar"
    assert (h.estado_anterior, h.estado_nuevo) == ("pendiente", "asignada")
    assert h.usuario_id == "usr-administrador"
    assert h.nota == "Que lo mire el mantenedor"
    assert h.detalle["especialista_id"] == "esp-porteros"
    assert h.detalle["aviso"] == "pendiente_envio"
    auditoria = await repo.listar_auditoria()
    assert any(
        a["accion"] == "asignar_incidencia" and a["detalle"]["aviso"] == "pendiente_envio"
        for a in auditoria
    )
    # El detalle ya muestra el historial
    d = (await cliente.get("/api/bandeja/incidencias/inc-portero")).json()
    assert d["historial"][0]["accion"] == "asignar"
    assert d["especialista"]["nombre"] == "Porteros Levante"
    # Ya no está pendiente del administrador
    filas = (await cliente.get("/api/bandeja/incidencias?pendientes_administrador=true")).json()
    assert [f["id"] for f in filas] == ["inc-limpieza"]


async def test_asignar_rechaza_especialistas_ajenos(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _entrar(cliente, repo, "owner")
    from building_call.domain.entidades import Administrador, Especialista

    repo.administradores["adm-otro"] = Administrador(id="adm-otro", nombre="Otra SL")
    repo.especialistas["esp-ajeno"] = Especialista(
        id="esp-ajeno",
        administrador_id="adm-otro",
        nombre="Ajeno",
        categoria_principal="fontaneria",
        canal_contacto="correo",
        destino="ajeno@demo.test",
    )
    base = "/api/bandeja/incidencias/inc-portero/asignar"
    assert (await cliente.post(base, json={"especialista_id": "esp-ajeno"})).status_code == 422
    assert (await cliente.post(base, json={"especialista_id": "esp-nada"})).status_code == 422
    assert repo.incidencias["inc-portero"].especialista_id is None
    assert await repo.listar_historial_incidencia("inc-portero") == []


async def test_cambiar_estado_queda_en_historial_y_auditoria(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _entrar(cliente, repo, "owner")
    base = "/api/bandeja/incidencias/inc-grifo/estado"
    for estado in ("en_curso", "esperando_informacion", "resuelta", "cerrada"):
        r = await cliente.post(base, json={"estado": estado})
        assert r.status_code == 200, r.text
        assert r.json()["incidencia"]["estado"] == estado
    historial = await repo.listar_historial_incidencia("inc-grifo")
    assert [(h.estado_anterior, h.estado_nuevo) for h in historial] == [
        ("asignada", "en_curso"),
        ("en_curso", "esperando_informacion"),
        ("esperando_informacion", "resuelta"),
        ("resuelta", "cerrada"),
    ]
    assert all(h.accion == "cambiar_estado" for h in historial)
    acciones = [a["accion"] for a in await repo.listar_auditoria()]
    assert acciones.count("cambiar_estado_incidencia") == 4
    # Cerrada es final: ni cambia de estado, ni se asigna
    assert (await cliente.post(base, json={"estado": "en_curso"})).status_code == 409
    r = await cliente.post(
        "/api/bandeja/incidencias/inc-grifo/asignar", json={"especialista_id": "esp-fontanero"}
    )
    assert r.status_code == 409


async def test_cambiar_estado_valida_el_destino(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    await _entrar(cliente, repo, "owner")
    base = "/api/bandeja/incidencias/inc-portero/estado"
    # "asignada" solo se alcanza asignando; "pendiente" no es un destino manual
    assert (await cliente.post(base, json={"estado": "asignada"})).status_code == 422
    assert (await cliente.post(base, json={"estado": "pendiente"})).status_code == 422
    assert (await cliente.post(base, json={"estado": "inventado"})).status_code == 422
    r = await cliente.post("/api/bandeja/incidencias/inc-nada/estado", json={"estado": "cerrada"})
    assert r.status_code == 404


async def test_nota_no_cambia_el_estado(cliente: AsyncClient, repo: RepositorioMemoria) -> None:
    await _entrar(cliente, repo, "administrador")
    base = "/api/bandeja/incidencias/inc-portero/nota"
    assert (await cliente.post(base, json={"texto": "   "})).status_code == 422
    r = await cliente.post(base, json={"texto": "Llamo al seguro mañana"})
    assert r.status_code == 200
    assert r.json()["incidencia"]["estado"] == "pendiente"
    (h,) = await repo.listar_historial_incidencia("inc-portero")
    assert h.accion == "nota" and h.nota == "Llamo al seguro mañana"
    assert h.estado_anterior == h.estado_nuevo == "pendiente"
    assert any(a["accion"] == "nota_incidencia" for a in await repo.listar_auditoria())
