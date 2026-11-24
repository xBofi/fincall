"""Acceso con Google y Microsoft, solicitudes de acceso y gestión de usuarios (solo owner)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from session_helpers import TEST_SECRET

from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.oidc import PERSONAL_ACCOUNTS_TENANT, OidcError, Provider, verify_id_token
from building_call.repos.memory import MemoryRepository
from building_call.settings import settings as global_settings

OWNER = "owner@example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeOidc:
    """El proveedor de pruebas: devuelve un id_token firmado con nuestra clave."""

    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}
        self.nonce = ""

    async def exchange(self, p: Provider, code: str, verifier: str, callback: str) -> str:
        assert code == "codigo-valido" and verifier
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": p.client_id,
            "iat": now,
            "exp": now + 600,
            "nonce": self.nonce,
            **self.claims,
        }
        return jwt.encode(claims, KEY, algorithm="RS256")

    async def key(self, p: Provider, token: str) -> Any:
        return KEY.public_key()


@pytest.fixture
def oidc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeOidc:
    for k, v in {
        "google_client_id": "cliente-google",
        "google_client_secret": "secreto-google",
        "microsoft_client_id": "cliente-ms",
        "microsoft_client_secret": "secreto-ms",
        "owner_email": OWNER,
        "console_url": "http://localhost:5173",
        "audio_dir": str(tmp_path / "audio"),
        "session_secret": TEST_SECRET,
    }.items():
        monkeypatch.setattr(global_settings, k, v)
    return FakeOidc()


@pytest.fixture
async def app(deps: Dependencies, oidc: FakeOidc):  # type: ignore[no-untyped-def]
    return create_app(deps, streaming_factory=None, oidc=oidc)  # type: ignore[arg-type]


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")


async def _log_in(c: AsyncClient, oidc: FakeOidc, provider: str = "google", **claims: Any) -> int:
    """Ida al proveedor y vuelta, como el navegador. Devuelve el estado de la vuelta."""
    outbound = await c.get(f"/api/auth/oidc/{provider}/start")
    assert outbound.status_code == 302
    query = parse_qs(urlparse(outbound.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"http://localhost:5173/api/auth/oidc/{provider}/callback"]
    oidc.nonce = query["nonce"][0]
    oidc.claims = claims
    vuelta = await c.get(
        f"/api/auth/oidc/{provider}/callback",
        params={"code": "codigo-valido", "state": query["state"][0]},
    )
    assert vuelta.status_code == 302
    return 0 if vuelta.headers["location"].endswith("?access=error") else 1


async def _me(c: AsyncClient) -> dict[str, Any]:
    return (await c.get("/api/auth/state")).json()


# ----------------------------------------------------------------------------- entrada


async def test_configured_providers(app: Any) -> None:
    async with _client(app) as c:
        r = await c.get("/api/auth/providers")
    assert r.json() == [
        {"id": "google", "label": "Google"},
        {"id": "microsoft", "label": "Microsoft"},
    ]


async def test_the_owner_gets_in_directly(app: Any, oidc: FakeOidc) -> None:
    async with _client(app) as c:
        assert await _log_in(c, oidc, sub="g-1", email=OWNER, email_verified=True, name="Owner")
        me = await _me(c)
        assert me["authenticated"] and me["user"]["role"] == "owner"
        assert me["user"]["access_state"] == "granted"
        assert "subject" not in me["user"] and "password_hash" not in me["user"]
        assert (await c.get("/api/operations/summary")).status_code == 200


async def test_unverified_owner_email_gives_no_access(app: Any, oidc: FakeOidc) -> None:
    async with _client(app) as c:
        assert await _log_in(c, oidc, sub="g-2", email=OWNER, email_verified=False)
        me = await _me(c)
        assert me["user"]["access_state"] == "not_requested"
        assert (await c.get("/api/operations/summary")).status_code == 403


async def test_another_person_requests_and_the_owner_grants(app: Any, oidc: FakeOidc) -> None:
    async with _client(app) as owner, _client(app) as marta:
        await _log_in(owner, oidc, sub="g-1", email=OWNER, email_verified=True)
        await _log_in(marta, oidc, sub="g-9", email="marta@ejemplo.es", email_verified=True)
        # Marta tiene sesión pero no acceso: solo puede ver su estado y solicitar
        assert (await _me(marta))["user"]["access_state"] == "not_requested"
        assert (await marta.get("/api/estates")).status_code == 403
        assert (await marta.get("/api/users")).status_code == 403
        r = await marta.post("/api/auth/submit_request")
        assert r.json()["access_state"] == "requested" and r.json()["requested_at"]

        # El owner ve la solicitud y concede con un rol
        users = (await owner.get("/api/users")).json()
        requested_user = next(u for u in users if u["email"] == "marta@ejemplo.es")
        assert requested_user["access_state"] == "requested" and requested_user["provider"] == "google"
        r = await owner.post(
            f"/api/users/{requested_user['id']}/grant", json={"role": "administrator"}
        )
        assert r.json()["access_state"] == "granted" and r.json()["role"] == "administrator"
        assert (await marta.get("/api/estates")).status_code == 200

        # Denegar le quita el acceso al momento (sus sesiones se revocan)
        await owner.post(f"/api/users/{requested_user['id']}/deny")
        assert (await marta.get("/api/estates")).status_code == 401
        actions = {a["action"] for a in await app.state.repo.list_audit()}
        assert {
            "provider_signup",
            "request_access",
            "grant_access",
            "deny_access",
        } <= actions


async def test_signing_in_again_recognises_the_person(
    app: Any, oidc: FakeOidc, repo: MemoryRepository
) -> None:
    async with _client(app) as c:
        await _log_in(c, oidc, sub="g-1", email=OWNER, email_verified=True)
        await _log_in(c, oidc, sub="g-1", email=OWNER, email_verified=True)
    assert len(await repo.list_users()) == 1


async def test_tampered_token_or_foreign_state_does_not_get_in(app: Any, oidc: FakeOidc) -> None:
    async with _client(app) as c:
        outbound = await c.get("/api/auth/oidc/google/start")
        r = await c.get(
            "/api/auth/oidc/google/callback", params={"code": "codigo-valido", "state": "other"}
        )
        assert r.headers["location"].endswith("?access=error")
        # Sin la cookie de la ida (otro navegador) tampoco
        query = parse_qs(urlparse(outbound.headers["location"]).query)
        async with _client(app) as unrelated:
            r = await unrelated.get(
                "/api/auth/oidc/google/callback",
                params={"code": "codigo-valido", "state": query["state"][0]},
            )
            assert r.headers["location"].endswith("?access=error")
        assert not (await _me(c))["authenticated"]


async def test_without_configured_provider(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_settings, "microsoft_client_id", "")
    async with _client(app) as c:
        assert (await c.get("/api/auth/oidc/microsoft/start")).status_code == 404


async def test_the_session_lasts_and_extends_itself(
    app: Any, oidc: FakeOidc, repo: MemoryRepository
) -> None:
    from datetime import UTC, datetime, timedelta

    async with _client(app) as c:
        await _log_in(c, oidc, sub="g-1", email=OWNER, email_verified=True)
        session = next(iter(repo.sessions.values()))
        assert session.expires_at - datetime.now(UTC) > timedelta(days=29)
        session.expires_at = datetime.now(UTC) + timedelta(days=3)  # ya pasó la mitad
        assert (await c.get("/api/auth/state")).json()["authenticated"]
        assert session.expires_at - datetime.now(UTC) > timedelta(days=29)


# ----------------------------------------------------------------------------- gestión


async def test_cannot_remove_the_last_owner_or_oneself(app: Any, oidc: FakeOidc) -> None:
    async with _client(app) as owner, _client(app) as other:
        await _log_in(owner, oidc, sub="g-1", email=OWNER, email_verified=True)
        me = (await _me(owner))["user"]
        assert (await owner.delete(f"/api/users/{me['id']}")).status_code == 409
        assert (await owner.post(f"/api/users/{me['id']}/deny")).status_code == 409
        assert (
            await owner.patch(f"/api/users/{me['id']}", json={"role": "quality"})
        ).status_code == 409
        await _log_in(other, oidc, sub="g-5", email="otro@ejemplo.es", email_verified=True)
        other_id = (await _me(other))["user"]["id"]
        await owner.post(f"/api/users/{other_id}/grant", json={"role": "owner"})
        # Con dos owners, 'otro' puede degradar al primero; luego 'otro' es el último
        assert (
            await other.patch(f"/api/users/{me['id']}", json={"role": "quality"})
        ).status_code == 200
        assert (await owner.get("/api/users")).status_code == 403
        await other.patch(f"/api/users/{me['id']}", json={"role": "owner"})
        assert (await owner.delete(f"/api/users/{other_id}")).status_code == 204


async def test_the_call_apis_require_session(app: Any, oidc: FakeOidc) -> None:
    async with _client(app) as c:
        assert (await c.post("/api/calls", json={})).status_code == 401
        assert (await c.get("/api/health")).status_code == 200  # la salud sigue siendo pública
        await _log_in(c, oidc, sub="g-1", email=OWNER, email_verified=True)
        assert (await c.post("/api/calls", json={})).status_code == 201


# ----------------------------------------------------------------------------- id_token


def _provider(name: str) -> Provider:
    return Provider(name, name, "a", "t", "j", "client", "s")


def _token(**claims: Any) -> str:
    now = int(time.time())
    return jwt.encode(
        {"aud": "client", "iat": now, "exp": now + 600, "sub": "s", "nonce": "n", **claims},
        KEY,
        algorithm="RS256",
    )


def test_microsoft_only_verifies_email_of_personal_accounts() -> None:
    ms = _provider("microsoft")
    personal = PERSONAL_ACCOUNTS_TENANT
    t = _token(iss=f"https://login.microsoftonline.com/{personal}/v2.0", tid=personal, email=OWNER)
    assert verify_id_token(ms, t, KEY.public_key(), "n").verified_email == OWNER
    # En un inquilino de empresa el correo lo pone su administrador: no vale para ser owner
    company = "11111111-2222-3333-4444-555555555555"
    t = _token(iss=f"https://login.microsoftonline.com/{company}/v2.0", tid=company, email=OWNER)
    identity = verify_id_token(ms, t, KEY.public_key(), "n")
    assert identity.verified_email is None and identity.email == OWNER


def test_id_token_with_wrong_issuer_audience_or_nonce() -> None:
    g = _provider("google")
    with pytest.raises(OidcError):
        verify_id_token(g, _token(iss="https://malo.example"), KEY.public_key(), "n")
    with pytest.raises(OidcError):
        verify_id_token(g, _token(iss="https://accounts.google.com"), KEY.public_key(), "other")
    with pytest.raises(OidcError):
        t = _token(iss="https://accounts.google.com", aud="otro-cliente")
        verify_id_token(g, t, KEY.public_key(), "n")
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OidcError):
        verify_id_token(g, _token(iss="https://accounts.google.com"), other.public_key(), "n")


async def test_unverified_email_is_neither_stored_nor_shown(app: Any, oidc: FakeOidc) -> None:
    """Revisión de seguridad 2026-10-22: en un inquilino propio de Microsoft cualquiera se pone
    el correo y el nombre de otra persona; en Solicitudes aparecía como si fuera ella."""
    company = "11111111-2222-3333-4444-555555555555"
    async with _client(app) as owner, _client(app) as fake:
        await _log_in(owner, oidc, sub="g-1", email=OWNER, email_verified=True)
        await _log_in(
            fake,
            oidc,
            provider="microsoft",
            sub="ms-9",
            tid=company,
            iss=f"https://login.microsoftonline.com/{company}/v2.0",
            email="pablo@empresa.es",
            name="Pablo",
        )
        me = (await _me(fake))["user"]
        assert me["email"] is None
        assert "pablo@empresa.es" not in me["username"]
