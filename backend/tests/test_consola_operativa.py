"""Acceso, APIs protegidas, trazabilidad por turno y audio con URL firmada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from building_call.acceso import Acceso
from building_call.api.main import crear_app
from building_call.audio_store import AlmacenAudio
from building_call.domain.operaciones import Rol, Usuario
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import Settings
from building_call.settings import settings as settings_globales


@pytest.fixture
async def cliente(deps: Dependencias, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(settings_globales, "sesion_secreto", "secreto-de-prueba")
    monkeypatch.setattr(settings_globales, "auth_modo", "google")
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _conversar(cliente: AsyncClient, turnos: list[str]) -> str:
    lid = (await cliente.post("/api/llamadas", json={"telefono_llamante": "+34600"})).json()[
        "llamada_id"
    ]
    for t in turnos:
        await cliente.post(f"/api/llamadas/{lid}/mensajes", json={"texto": t})
    return lid


async def _sesion(cliente: AsyncClient, repo: RepositorioMemoria, rol: Rol) -> None:
    """Abre una sesión como haría el callback de Google, sin pasar por Google."""
    acceso = Acceso(repo, Settings(sesion_secreto="s", sesion_horas=1))
    usuario = Usuario(
        id=f"usr-{rol}", google_sub=f"sub-{rol}", email=f"{rol}@x.es", rol=rol, activo=True
    )
    from fastapi import Request, Response

    resp = Response()
    req = Request(
        {
            "type": "http",
            "headers": [],
            "scheme": "http",
            "server": ("localhost", 80),
            "path": "/",
            "query_string": b"",
        }
    )
    token = await acceso.abrir_sesion(usuario, resp, req)
    cliente.cookies.set("bc_sesion", token)


async def test_sin_sesion_las_apis_operativas_responden_401(cliente: AsyncClient) -> None:
    assert (await cliente.get("/api/operaciones/resumen")).status_code == 401
    assert (await cliente.get("/api/operaciones/llamadas")).status_code == 401
    estado = (await cliente.get("/api/auth/estado")).json()
    assert estado["autenticado"] is False and estado["modo"] == "deshabilitado"
    assert (await cliente.get("/api/auth/login")).status_code == 503  # sin Google ni allowlist


async def test_revisor_ve_expedientes_transcripcion_y_decisiones(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    lid = await _conversar(
        cliente, ["Calle Mayor 12", "sí", "3 B", "Marta", "Gotea el grifo", "sí"]
    )
    await _sesion(cliente, repo, Rol.calidad)
    assert (await cliente.get("/api/auth/me")).json()["rol"] == "calidad"

    resumen = (await cliente.get("/api/operaciones/resumen")).json()
    assert resumen["llamadas"] == 1 and resumen["resueltas"] == 1

    lista = (await cliente.get("/api/operaciones/llamadas")).json()
    assert lista[0]["id"] == lid and lista[0]["estado"] == "finalizada"

    detalle = (await cliente.get(f"/api/operaciones/llamadas/{lid}")).json()
    assert detalle["llamada"]["resultado"] == "incidencia_creada"
    assert len(detalle["turnos"]) == 7  # saludo + 6 turnos
    assert detalle["turnos"][1]["vecino"] == "Calle Mayor 12"
    assert any(d["nodo"] == "crear_ticket" for d in detalle["decisiones"])

    transcripcion = (await cliente.get(f"/api/operaciones/llamadas/{lid}/transcripcion")).json()
    assert transcripcion[0]["rol"] == "bot"

    # La auditoría exige owner/administrador: calidad no puede
    assert (await cliente.get("/api/operaciones/auditoria")).status_code == 403


async def test_owner_ve_auditoria_y_logout_revoca(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    lid = await _conversar(cliente, ["Calle Mayor 12"])
    await _sesion(cliente, repo, Rol.owner)
    await cliente.get(f"/api/operaciones/llamadas/{lid}")
    auditoria = (await cliente.get("/api/operaciones/auditoria")).json()
    assert any(a["accion"] == "ver_expediente" for a in auditoria)
    assert any(a["accion"] == "login" for a in auditoria)
    assert (await cliente.post("/api/auth/logout")).status_code == 200
    cliente.cookies.clear()
    assert (await cliente.get("/api/operaciones/resumen")).status_code == 401


async def test_audio_privado_url_firmada_y_retencion(
    repo: RepositorioMemoria, tmp_path: Path
) -> None:
    almacen = AlmacenAudio(tmp_path, "secreto", retencion_dias=14, url_segundos=60)
    pista = await almacen.guardar_pcm(repo, "llam-1", "cliente", b"\x00\x01" * 1600, 16000)
    assert pista is not None and Path(pista["clave_objeto"]).exists()
    assert pista["duracion_ms"] == 100
    url = almacen.url_firmada(pista["id"])
    exp = int(url.split("exp=")[1].split("&")[0])
    firma = url.split("firma=")[1]
    assert almacen.firma_valida(pista["id"], exp, firma)
    assert not almacen.firma_valida(pista["id"], exp, "0" * 64)
    caducada = datetime.now(UTC) + timedelta(seconds=120)
    assert not almacen.firma_valida(pista["id"], exp, firma, ahora=caducada)
    # Retención: a los 14 días se borra el fichero y se marca la fila
    borradas = await almacen.purgar_vencidas(repo, ahora=datetime.now(UTC) + timedelta(days=15))
    assert borradas == 1
    assert not Path(pista["clave_objeto"]).exists()
    assert (await repo.obtener_pista(pista["id"]))["eliminada_en"] is not None
    assert almacen.leer(await repo.obtener_pista(pista["id"])) is None


async def test_audio_por_api_exige_sesion_y_firma(
    cliente: AsyncClient, repo: RepositorioMemoria, tmp_path: Path
) -> None:
    almacen = AlmacenAudio(tmp_path / "audio", "secreto-de-prueba", url_segundos=60)
    pista = await almacen.guardar_pcm(repo, "llam-x", "cliente", b"\x00\x01" * 160, 16000)
    assert pista is not None
    url = almacen.url_firmada(pista["id"])
    assert (await cliente.get(url)).status_code == 401
    await _sesion(cliente, repo, Rol.administrador)
    r = await cliente.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert (
        await cliente.get(f"/api/operaciones/audio/{pista['id']}?exp=1&firma=x")
    ).status_code == 403


async def test_modo_desarrollo_da_owner_local(
    deps: Dependencias, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_globales, "auth_modo", "desarrollo")
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        salud = (await c.get("/api/salud")).json()
        assert salud["acceso"] == "desarrollo"
        assert (await c.get("/api/operaciones/resumen")).status_code == 200
        assert (await c.get("/api/auth/me")).json()["rol"] == "owner"
