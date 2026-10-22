"""Arreglos de la revisión de seguridad del 2026-10-22 (los de severidad media y baja)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.acceso import SESION_MAX_DIAS, Acceso
from building_call.api.main import crear_app
from building_call.auth import hash_token_sesion
from building_call.domain.operaciones import EstadoAcceso, Rol, Usuario
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import Settings
from building_call.settings import settings as settings_globales


def _cliente(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")


async def test_un_texto_enorme_no_llega_al_modelo(deps: Dependencias) -> None:
    app = crear_app(deps, fabrica_streaming=None)
    async with _cliente(app) as c:
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
        lid = (await c.post("/api/llamadas", json={})).json()["llamada_id"]
        enorme = "palabra " * 5000
        assert (
            await c.post(f"/api/llamadas/{lid}/parcial", json={"texto": enorme})
        ).status_code == 422
        assert (
            await c.post(f"/api/llamadas/{lid}/mensajes", json={"texto": enorme})
        ).status_code == 422


async def test_un_secreto_de_agente_corto_no_da_acceso(
    deps: Dependencias, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_globales, "agente_secreto", "agente")
    app = crear_app(deps, fabrica_streaming=None)
    async with _cliente(app) as c:
        r = await c.post("/api/llamadas", json={}, headers={"Authorization": "Bearer agente"})
        assert r.status_code == 401


async def test_livekit_con_claves_de_desarrollo_no_se_expone(
    deps: Dependencias, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {
        "livekit_url": "ws://localhost:7880",
        "livekit_api_key": "devkey",
        "livekit_api_secret": "secret",
        "livekit_url_consola": "/",
    }.items():
        monkeypatch.setattr(settings_globales, k, v)
    app = crear_app(deps, fabrica_streaming=None)
    async with _cliente(app) as c:
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"llamada_id": "llam-1"})
        assert r.status_code == 503


def test_la_cookie_es_secure_si_la_consola_publica_es_https() -> None:
    from starlette.requests import Request

    pedir = Request(
        {
            "type": "http",
            "headers": [],
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "path": "/",
        }
    )
    local = Acceso(RepositorioMemoria(), Settings(_env_file=None))  # type: ignore[arg-type]
    publica = Acceso(
        RepositorioMemoria(),  # type: ignore[arg-type]
        Settings(_env_file=None, url_consola="https://consola.ejemplo.es"),
    )
    assert local._segura(pedir) is False
    assert publica._segura(pedir) is True


async def test_la_sesion_caduca_aunque_se_siga_usando(repo: RepositorioMemoria) -> None:
    token = sesion_de_prueba(repo)
    sesion = repo.sesiones[hash_token_sesion(token)]
    sesion.creada_en = datetime.now(UTC) - timedelta(days=SESION_MAX_DIAS + 1)
    sesion.expira_en = datetime.now(UTC) + timedelta(days=10)
    from starlette.requests import Request

    pedir = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"bc_sesion={token}".encode())],
            "scheme": "http",
            "server": ("localhost", 80),
            "path": "/",
        }
    )
    acceso = Acceso(repo, Settings(_env_file=None))  # type: ignore[arg-type]
    assert await acceso.persona_de_peticion(pedir) is None


async def test_quien_fue_denegado_no_puede_insistir_el_mismo_dia(deps: Dependencias) -> None:
    repo: RepositorioMemoria = deps.repo  # type: ignore[assignment]
    token = sesion_de_prueba(repo, "calidad")
    usuario = repo.usuarios["usr-calidad"]
    repo.usuarios["usr-calidad"] = Usuario(
        **{
            **usuario.model_dump(),
            "rol": Rol.calidad,
            "estado_acceso": EstadoAcceso.denegado,
            "solicitado_en": datetime.now(UTC) - timedelta(hours=2),
        }
    )
    app = crear_app(deps, fabrica_streaming=None)
    async with _cliente(app) as c:
        c.cookies.set("bc_sesion", token)
        assert (await c.post("/api/auth/solicitar")).status_code == 429
