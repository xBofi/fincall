"""Login local, cambio de contraseña obligatorio y gestión de usuarios (solo owner)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SECRETO_DE_PRUEBA
from httpx import ASGITransport, AsyncClient

from building_call.acceso import Acceso
from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import Settings
from building_call.settings import settings as settings_globales

CLAVE_ADMIN = "clave-nueva-segura"


@pytest.fixture
async def cliente(deps: Dependencias, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(settings_globales, "sesion_secreto", SECRETO_DE_PRUEBA)
    # El ciclo de vida (que crea el admin inicial) no corre bajo ASGITransport: lo hacemos aquí
    await Acceso(deps.repo, Settings(_env_file=None)).asegurar_admin_inicial()  # type: ignore[arg-type]
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


def _otro_cliente(c: AsyncClient) -> AsyncClient:
    return AsyncClient(transport=c._transport, base_url="http://localhost")


async def _login(c: AsyncClient, usuario: str, contrasena: str) -> int:
    r = await c.post("/api/auth/login", json={"usuario": usuario, "contrasena": contrasena})
    return r.status_code


async def _cambiar(c: AsyncClient, actual: str, nueva: str) -> int:
    return (
        await c.post("/api/auth/contrasena", json={"actual": actual, "nueva": nueva})
    ).status_code


async def _entrar_como_admin(c: AsyncClient) -> None:
    assert await _login(c, "admin", "admin") == 200
    assert await _cambiar(c, "admin", CLAVE_ADMIN) == 200


async def test_admin_inicial_es_idempotente(cliente: AsyncClient, repo: RepositorioMemoria) -> None:
    acceso = Acceso(repo, Settings(_env_file=None))
    assert await acceso.asegurar_admin_inicial() is None  # ya lo creó la fixture
    usuarios = await repo.listar_usuarios()
    assert [u.nombre_usuario for u in usuarios] == ["admin"]
    assert usuarios[0].rol == "owner" and usuarios[0].debe_cambiar_contrasena


async def test_login_incorrecto_y_bloqueo_por_intentos(cliente: AsyncClient) -> None:
    assert await _login(cliente, "admin", "mal") == 401
    assert await _login(cliente, "noexiste", "mal") == 401
    estado = (await cliente.get("/api/auth/estado")).json()
    assert estado == {"modo": "local", "autenticado": False, "usuario": None}
    for _ in range(4):
        await _login(cliente, "admin", "mal")
    assert await _login(cliente, "admin", "admin") == 429  # bloqueado aunque sea correcta


async def test_primer_acceso_obliga_a_cambiar_la_contrasena(cliente: AsyncClient) -> None:
    assert await _login(cliente, "admin", "admin") == 200
    yo = (await cliente.get("/api/auth/me")).json()
    assert yo["debe_cambiar_contrasena"] is True and "hash_contrasena" not in yo
    assert (await cliente.get("/api/operaciones/resumen")).status_code == 403
    assert (await cliente.get("/api/usuarios")).status_code == 403
    assert await _cambiar(cliente, "mal", CLAVE_ADMIN) == 400
    assert await _cambiar(cliente, "admin", "corta") == 400
    assert await _cambiar(cliente, "admin", CLAVE_ADMIN) == 200
    assert (await cliente.get("/api/operaciones/resumen")).status_code == 200
    # La contraseña antigua deja de valer; la nueva sí
    cliente.cookies.clear()
    assert await _login(cliente, "admin", "admin") == 401
    assert await _login(cliente, "admin", CLAVE_ADMIN) == 200


async def test_owner_gestiona_usuarios(cliente: AsyncClient) -> None:
    await _entrar_como_admin(cliente)
    r = await cliente.post("/api/usuarios", json={"nombre_usuario": "Marta", "rol": "calidad"})
    assert r.status_code == 201
    marta = r.json()
    temporal = marta["contrasena_temporal"]
    assert marta["nombre_usuario"] == "marta" and temporal and marta["debe_cambiar_contrasena"]
    repetido = await cliente.post(
        "/api/usuarios", json={"nombre_usuario": "marta", "rol": "calidad"}
    )
    assert repetido.status_code == 409
    nombres = [u["nombre_usuario"] for u in (await cliente.get("/api/usuarios")).json()]
    assert nombres == ["admin", "marta"]

    # Marta entra con la temporal y solo puede cambiar su contraseña
    c2 = _otro_cliente(cliente)
    assert await _login(c2, "marta", temporal) == 200
    assert (await c2.get("/api/operaciones/resumen")).status_code == 403
    assert await _cambiar(c2, temporal, "otra-clave-larga") == 200
    assert (await c2.get("/api/operaciones/resumen")).status_code == 200
    assert (await c2.get("/api/usuarios")).status_code == 403  # calidad no gestiona usuarios

    # Cambio de rol, restablecimiento (revoca sus sesiones) y desactivación
    r = await cliente.patch(f"/api/usuarios/{marta['id']}", json={"rol": "administrador"})
    assert r.json()["rol"] == "administrador"
    r = await cliente.post(f"/api/usuarios/{marta['id']}/restablecer")
    nueva_temporal = r.json()["contrasena_temporal"]
    assert (await c2.get("/api/auth/me")).status_code == 401
    assert await _login(c2, "marta", nueva_temporal) == 200
    await cliente.patch(f"/api/usuarios/{marta['id']}", json={"activo": False})
    assert (await c2.get("/api/auth/me")).status_code == 401
    assert await _login(c2, "marta", nueva_temporal) == 401

    # Baja: desaparece y su rastro queda en la auditoría
    assert (await cliente.delete(f"/api/usuarios/{marta['id']}")).status_code == 204
    assert (await cliente.delete(f"/api/usuarios/{marta['id']}")).status_code == 404
    acciones = {a["accion"] for a in (await cliente.get("/api/operaciones/auditoria")).json()}
    assert {
        "crear_usuario",
        "modificar_usuario",
        "restablecer_contrasena",
        "eliminar_usuario",
    } <= acciones


async def test_no_se_puede_quitar_al_ultimo_owner_ni_a_uno_mismo(cliente: AsyncClient) -> None:
    await _entrar_como_admin(cliente)
    yo = (await cliente.get("/api/auth/me")).json()
    assert (await cliente.delete(f"/api/usuarios/{yo['id']}")).status_code == 409
    assert (
        await cliente.patch(f"/api/usuarios/{yo['id']}", json={"rol": "calidad"})
    ).status_code == 409

    otro = (
        await cliente.post("/api/usuarios", json={"nombre_usuario": "otro", "rol": "owner"})
    ).json()
    c2 = _otro_cliente(cliente)
    assert await _login(c2, "otro", otro["contrasena_temporal"]) == 200
    assert await _cambiar(c2, otro["contrasena_temporal"], "clave-de-otro-larga") == 200
    # Con dos owners activos, 'otro' puede degradar a 'admin'...
    assert (await c2.patch(f"/api/usuarios/{yo['id']}", json={"rol": "calidad"})).status_code == 200
    # ...pero ahora 'otro' es el último owner activo: 'admin' (calidad) ya no gestiona, y nadie
    # puede desactivarlo ni degradarlo
    assert (await cliente.get("/api/usuarios")).status_code == 403
    await c2.patch(f"/api/usuarios/{yo['id']}", json={"rol": "owner"})
    assert (
        await cliente.patch(f"/api/usuarios/{otro['id']}", json={"activo": False})
    ).status_code == 200
    assert (
        await cliente.patch(f"/api/usuarios/{yo['id']}", json={"activo": False})
    ).status_code == 409


async def test_las_apis_de_llamada_exigen_sesion(cliente: AsyncClient) -> None:
    assert (
        await cliente.post("/api/llamadas", json={"telefono_llamante": "+34600"})
    ).status_code == 401
    assert (await cliente.get("/api/fincas")).status_code == 401
    assert (await cliente.get("/api/salud")).status_code == 200  # la salud sigue siendo pública
    await _entrar_como_admin(cliente)
    assert (
        await cliente.post("/api/llamadas", json={"telefono_llamante": "+34600"})
    ).status_code == 201
