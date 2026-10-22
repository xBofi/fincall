"""Acceso con Google y Microsoft, solicitudes de acceso y gestión de usuarios (solo owner)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from conftest import SECRETO_DE_PRUEBA
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias
from building_call.oidc import TENANT_CUENTAS_PERSONALES, ErrorOidc, Proveedor, verificar_id_token
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import settings as settings_globales

OWNER = "owner@example.com"
CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class OidcFalso:
    """El proveedor de pruebas: devuelve un id_token firmado con nuestra clave."""

    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}
        self.nonce = ""

    async def canjear(self, p: Proveedor, codigo: str, verificador: str, retorno: str) -> str:
        assert codigo == "codigo-valido" and verificador
        ahora = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": p.client_id,
            "iat": ahora,
            "exp": ahora + 600,
            "nonce": self.nonce,
            **self.claims,
        }
        return jwt.encode(claims, CLAVE, algorithm="RS256")

    async def clave(self, p: Proveedor, token: str) -> Any:
        return CLAVE.public_key()


@pytest.fixture
def oidc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> OidcFalso:
    for k, v in {
        "google_client_id": "cliente-google",
        "google_client_secret": "secreto-google",
        "microsoft_client_id": "cliente-ms",
        "microsoft_client_secret": "secreto-ms",
        "owner_email": OWNER,
        "url_consola": "http://localhost:5173",
        "audio_dir": str(tmp_path / "audio"),
        "sesion_secreto": SECRETO_DE_PRUEBA,
    }.items():
        monkeypatch.setattr(settings_globales, k, v)
    return OidcFalso()


@pytest.fixture
async def app(deps: Dependencias, oidc: OidcFalso):  # type: ignore[no-untyped-def]
    return crear_app(deps, fabrica_streaming=None, oidc=oidc)  # type: ignore[arg-type]


def _cliente(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")


async def _entrar(c: AsyncClient, oidc: OidcFalso, proveedor: str = "google", **claims: Any) -> int:
    """Ida al proveedor y vuelta, como el navegador. Devuelve el estado de la vuelta."""
    ida = await c.get(f"/api/auth/oidc/{proveedor}/inicio")
    assert ida.status_code == 302
    query = parse_qs(urlparse(ida.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"http://localhost:5173/api/auth/oidc/{proveedor}/retorno"]
    oidc.nonce = query["nonce"][0]
    oidc.claims = claims
    vuelta = await c.get(
        f"/api/auth/oidc/{proveedor}/retorno",
        params={"code": "codigo-valido", "state": query["state"][0]},
    )
    assert vuelta.status_code == 302
    return 0 if vuelta.headers["location"].endswith("?acceso=error") else 1


async def _yo(c: AsyncClient) -> dict[str, Any]:
    return (await c.get("/api/auth/estado")).json()


# ----------------------------------------------------------------------------- entrada


async def test_proveedores_configurados(app: Any) -> None:
    async with _cliente(app) as c:
        r = await c.get("/api/auth/proveedores")
    assert r.json() == [
        {"id": "google", "etiqueta": "Google"},
        {"id": "microsoft", "etiqueta": "Microsoft"},
    ]


async def test_el_owner_entra_directamente(app: Any, oidc: OidcFalso) -> None:
    async with _cliente(app) as c:
        assert await _entrar(c, oidc, sub="g-1", email=OWNER, email_verified=True, name="Andreu")
        yo = await _yo(c)
        assert yo["autenticado"] and yo["usuario"]["rol"] == "owner"
        assert yo["usuario"]["estado_acceso"] == "concedido"
        assert "sujeto" not in yo["usuario"] and "hash_contrasena" not in yo["usuario"]
        assert (await c.get("/api/operaciones/resumen")).status_code == 200


async def test_correo_del_owner_sin_verificar_no_da_acceso(app: Any, oidc: OidcFalso) -> None:
    async with _cliente(app) as c:
        assert await _entrar(c, oidc, sub="g-2", email=OWNER, email_verified=False)
        yo = await _yo(c)
        assert yo["usuario"]["estado_acceso"] == "sin_solicitar"
        assert (await c.get("/api/operaciones/resumen")).status_code == 403


async def test_otra_persona_solicita_y_el_owner_concede(app: Any, oidc: OidcFalso) -> None:
    async with _cliente(app) as owner, _cliente(app) as marta:
        await _entrar(owner, oidc, sub="g-1", email=OWNER, email_verified=True)
        await _entrar(marta, oidc, sub="g-9", email="marta@ejemplo.es", email_verified=True)
        # Marta tiene sesión pero no acceso: solo puede ver su estado y solicitar
        assert (await _yo(marta))["usuario"]["estado_acceso"] == "sin_solicitar"
        assert (await marta.get("/api/fincas")).status_code == 403
        assert (await marta.get("/api/usuarios")).status_code == 403
        r = await marta.post("/api/auth/solicitar")
        assert r.json()["estado_acceso"] == "solicitado" and r.json()["solicitado_en"]

        # El owner ve la solicitud y concede con un rol
        usuarios = (await owner.get("/api/usuarios")).json()
        pedida = next(u for u in usuarios if u["email"] == "marta@ejemplo.es")
        assert pedida["estado_acceso"] == "solicitado" and pedida["proveedor"] == "google"
        r = await owner.post(
            f"/api/usuarios/{pedida['id']}/conceder", json={"rol": "administrador"}
        )
        assert r.json()["estado_acceso"] == "concedido" and r.json()["rol"] == "administrador"
        assert (await marta.get("/api/fincas")).status_code == 200

        # Denegar le quita el acceso al momento (sus sesiones se revocan)
        await owner.post(f"/api/usuarios/{pedida['id']}/denegar")
        assert (await marta.get("/api/fincas")).status_code == 401
        acciones = {a["accion"] for a in (await owner.get("/api/operaciones/auditoria")).json()}
        assert {
            "alta_por_proveedor",
            "solicitar_acceso",
            "conceder_acceso",
            "denegar_acceso",
        } <= acciones


async def test_volver_a_entrar_reconoce_a_la_persona(
    app: Any, oidc: OidcFalso, repo: RepositorioMemoria
) -> None:
    async with _cliente(app) as c:
        await _entrar(c, oidc, sub="g-1", email=OWNER, email_verified=True)
        await _entrar(c, oidc, sub="g-1", email=OWNER, email_verified=True)
    assert len(await repo.listar_usuarios()) == 1


async def test_token_manipulado_o_state_ajeno_no_entra(app: Any, oidc: OidcFalso) -> None:
    async with _cliente(app) as c:
        ida = await c.get("/api/auth/oidc/google/inicio")
        r = await c.get(
            "/api/auth/oidc/google/retorno", params={"code": "codigo-valido", "state": "otro"}
        )
        assert r.headers["location"].endswith("?acceso=error")
        # Sin la cookie de la ida (otro navegador) tampoco
        query = parse_qs(urlparse(ida.headers["location"]).query)
        async with _cliente(app) as ajeno:
            r = await ajeno.get(
                "/api/auth/oidc/google/retorno",
                params={"code": "codigo-valido", "state": query["state"][0]},
            )
            assert r.headers["location"].endswith("?acceso=error")
        assert not (await _yo(c))["autenticado"]


async def test_sin_proveedor_configurado(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_globales, "microsoft_client_id", "")
    async with _cliente(app) as c:
        assert (await c.get("/api/auth/oidc/microsoft/inicio")).status_code == 404


async def test_la_sesion_dura_y_se_alarga_sola(
    app: Any, oidc: OidcFalso, repo: RepositorioMemoria
) -> None:
    from datetime import UTC, datetime, timedelta

    async with _cliente(app) as c:
        await _entrar(c, oidc, sub="g-1", email=OWNER, email_verified=True)
        sesion = next(iter(repo.sesiones.values()))
        assert sesion.expira_en - datetime.now(UTC) > timedelta(days=29)
        sesion.expira_en = datetime.now(UTC) + timedelta(days=3)  # ya pasó la mitad
        assert (await c.get("/api/auth/estado")).json()["autenticado"]
        assert sesion.expira_en - datetime.now(UTC) > timedelta(days=29)


# ----------------------------------------------------------------------------- gestión


async def test_no_se_puede_quitar_al_ultimo_owner_ni_a_uno_mismo(app: Any, oidc: OidcFalso) -> None:
    async with _cliente(app) as owner, _cliente(app) as otro:
        await _entrar(owner, oidc, sub="g-1", email=OWNER, email_verified=True)
        yo = (await _yo(owner))["usuario"]
        assert (await owner.delete(f"/api/usuarios/{yo['id']}")).status_code == 409
        assert (await owner.post(f"/api/usuarios/{yo['id']}/denegar")).status_code == 409
        assert (
            await owner.patch(f"/api/usuarios/{yo['id']}", json={"rol": "calidad"})
        ).status_code == 409
        await _entrar(otro, oidc, sub="g-5", email="otro@ejemplo.es", email_verified=True)
        id_otro = (await _yo(otro))["usuario"]["id"]
        await owner.post(f"/api/usuarios/{id_otro}/conceder", json={"rol": "owner"})
        # Con dos owners, 'otro' puede degradar al primero; luego 'otro' es el último
        assert (
            await otro.patch(f"/api/usuarios/{yo['id']}", json={"rol": "calidad"})
        ).status_code == 200
        assert (await owner.get("/api/usuarios")).status_code == 403
        await otro.patch(f"/api/usuarios/{yo['id']}", json={"rol": "owner"})
        assert (await owner.delete(f"/api/usuarios/{id_otro}")).status_code == 204


async def test_las_apis_de_llamada_exigen_sesion(app: Any, oidc: OidcFalso) -> None:
    async with _cliente(app) as c:
        assert (await c.post("/api/llamadas", json={})).status_code == 401
        assert (await c.get("/api/salud")).status_code == 200  # la salud sigue siendo pública
        await _entrar(c, oidc, sub="g-1", email=OWNER, email_verified=True)
        assert (await c.post("/api/llamadas", json={})).status_code == 201


# ----------------------------------------------------------------------------- id_token


def _proveedor(nombre: str) -> Proveedor:
    return Proveedor(nombre, nombre, "a", "t", "j", "cliente", "s")


def _token(**claims: Any) -> str:
    ahora = int(time.time())
    return jwt.encode(
        {"aud": "cliente", "iat": ahora, "exp": ahora + 600, "sub": "s", "nonce": "n", **claims},
        CLAVE,
        algorithm="RS256",
    )


def test_microsoft_solo_verifica_correo_de_cuentas_personales() -> None:
    ms = _proveedor("microsoft")
    personal = TENANT_CUENTAS_PERSONALES
    t = _token(iss=f"https://login.microsoftonline.com/{personal}/v2.0", tid=personal, email=OWNER)
    assert verificar_id_token(ms, t, CLAVE.public_key(), "n").correo_verificado == OWNER
    # En un inquilino de empresa el correo lo pone su administrador: no vale para ser owner
    empresa = "11111111-2222-3333-4444-555555555555"
    t = _token(iss=f"https://login.microsoftonline.com/{empresa}/v2.0", tid=empresa, email=OWNER)
    identidad = verificar_id_token(ms, t, CLAVE.public_key(), "n")
    assert identidad.correo_verificado is None and identidad.correo == OWNER


def test_id_token_con_emisor_audiencia_o_nonce_incorrectos() -> None:
    g = _proveedor("google")
    with pytest.raises(ErrorOidc):
        verificar_id_token(g, _token(iss="https://malo.example"), CLAVE.public_key(), "n")
    with pytest.raises(ErrorOidc):
        verificar_id_token(g, _token(iss="https://accounts.google.com"), CLAVE.public_key(), "otro")
    with pytest.raises(ErrorOidc):
        t = _token(iss="https://accounts.google.com", aud="otro-cliente")
        verificar_id_token(g, t, CLAVE.public_key(), "n")
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ErrorOidc):
        verificar_id_token(g, _token(iss="https://accounts.google.com"), otra.public_key(), "n")


async def test_correo_sin_verificar_no_se_guarda_ni_se_muestra(app: Any, oidc: OidcFalso) -> None:
    """Revisión de seguridad 2026-10-22: en un inquilino propio de Microsoft cualquiera se pone
    el correo y el nombre de otra persona; en Solicitudes aparecía como si fuera ella."""
    empresa = "11111111-2222-3333-4444-555555555555"
    async with _cliente(app) as owner, _cliente(app) as falso:
        await _entrar(owner, oidc, sub="g-1", email=OWNER, email_verified=True)
        await _entrar(
            falso,
            oidc,
            proveedor="microsoft",
            sub="ms-9",
            tid=empresa,
            iss=f"https://login.microsoftonline.com/{empresa}/v2.0",
            email="marc@empresa.es",
            name="Marc",
        )
        yo = (await _yo(falso))["usuario"]
        assert yo["email"] is None
        assert "marc@empresa.es" not in yo["nombre_usuario"]
