"""Acceso, APIs protegidas, trazabilidad por turno y audio con URL firmada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import TEST_SECRET, make_session
from httpx import ASGITransport, AsyncClient

from building_call.access import Access
from building_call.api.main import MIN_SECRET_LENGTH, create_app, signing_secret
from building_call.audio_store import AudioStore
from building_call.domain.operations import Role, User
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.settings import Settings
from building_call.settings import settings as global_settings


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _converse(client: AsyncClient, turns: list[str]) -> str:
    # Iniciar y conversar exige sesión (como en la consola): un owner de prueba la abre
    repo = client._transport.app.state.repo  # type: ignore[attr-defined]
    client.cookies.set("bc_session", make_session(repo))
    cid = (await client.post("/api/calls", json={"caller_phone": "+34600"})).json()[
        "call_id"
    ]
    for t in turns:
        await client.post(f"/api/calls/{cid}/messages", json={"text": t})
    return cid


async def _session(client: AsyncClient, repo: MemoryRepository, role: Role) -> None:
    """Abre una sesión como haría el login, sin pasar por la contraseña."""
    access = Access(repo, Settings(_env_file=None, session_secret="s", session_days=1))
    user = User(
        id=f"usr-{role}", username=str(role), role=role, active=True, password_hash="x"
    )
    await repo.save_user(user)
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
    token = await access.open_session(user, resp, req)
    client.cookies.set("bc_session", token)


async def test_without_session_the_operational_apis_return_401(client: AsyncClient) -> None:
    assert (await client.get("/api/operations/summary")).status_code == 401
    assert (await client.get("/api/operations/calls")).status_code == 401
    state = (await client.get("/api/auth/state")).json()
    assert state["authenticated"] is False and state["mode"] == "oidc"
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_reviewer_sees_call_records_transcript_and_decisions(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    cid = await _converse(client, ["Calle Mayor 12", "sí", "3 B", "Gotea el grifo", "sí"])
    await _session(client, repo, Role.quality)
    assert (await client.get("/api/auth/me")).json()["role"] == "quality"

    summary = (await client.get("/api/operations/summary")).json()
    assert summary["calls"] == 1 and summary["resolved"] == 1

    items = (await client.get("/api/operations/calls")).json()
    assert items[0]["id"] == cid and items[0]["state"] == "finished"

    detail = (await client.get(f"/api/operations/calls/{cid}")).json()
    assert detail["call"]["result"] == "incident_created"
    assert len(detail["turns"]) == 6  # saludo + 5 turnos
    assert detail["turns"][1]["caller"] == "Calle Mayor 12"
    assert any(d["node"] == "create_ticket" for d in detail["decisions"])

    transcript = (await client.get(f"/api/operations/calls/{cid}/transcript")).json()
    assert transcript[0]["role"] == "bot"

    # La auditoría exige owner/administrador: calidad no puede
    assert (await client.get("/api/operations/audit")).status_code == 403


async def test_owner_sees_audit_and_logout_revokes(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    cid = await _converse(client, ["Calle Mayor 12"])
    await _session(client, repo, Role.owner)
    await client.get(f"/api/operations/calls/{cid}")
    audit = (await client.get("/api/operations/audit")).json()
    assert any(a["action"] == "view_call_record" for a in audit)
    assert any(a["action"] == "login" for a in audit)
    assert (await client.post("/api/auth/logout")).status_code == 200
    client.cookies.clear()
    assert (await client.get("/api/operations/summary")).status_code == 401


async def test_private_audio_signed_url_and_retention(
    repo: MemoryRepository, tmp_path: Path
) -> None:
    store = AudioStore(tmp_path, "secret", retention_days=14, url_seconds=60)
    track = await store.save_pcm(repo, "llam-1", "client", b"\x00\x01" * 1600, 16000)
    assert track is not None and Path(track["object_key"]).exists()
    assert track["duration_ms"] == 100
    url = store.signed_url(track["id"])
    exp = int(url.split("exp=")[1].split("&")[0])
    signature = url.split("signature=")[1]
    assert store.valid_signature(track["id"], exp, signature)
    assert not store.valid_signature(track["id"], exp, "0" * 64)
    expired = datetime.now(UTC) + timedelta(seconds=120)
    assert not store.valid_signature(track["id"], exp, signature, now=expired)
    # Retención: a los 14 días se borra el fichero y se marca la fila
    deleted_count = await store.purge_expired(repo, now=datetime.now(UTC) + timedelta(days=15))
    assert deleted_count == 1
    assert not Path(track["object_key"]).exists()
    assert (await repo.get_track(track["id"]))["deleted_at"] is not None
    assert store.read(await repo.get_track(track["id"])) is None


async def test_audio_via_api_requires_session_and_signature(
    client: AsyncClient, repo: MemoryRepository, tmp_path: Path
) -> None:
    store = AudioStore(tmp_path / "audio", TEST_SECRET, url_seconds=60)
    track = await store.save_pcm(repo, "llam-x", "client", b"\x00\x01" * 160, 16000)
    assert track is not None
    url = store.signed_url(track["id"])
    assert (await client.get(url)).status_code == 401
    await _session(client, repo, Role.administrator)
    r = await client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert (
        await client.get(f"/api/operations/audio/{track['id']}?exp=1&signature=x")
    ).status_code == 403


# ---------------------------------------------------------------- secreto de firma del audio


def test_real_mode_does_not_start_without_secret(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antes se caía a `"dev-" + tipo_de_repo`: una constante deducible del código fuente con la
    que cualquiera podía forjar una URL y bajarse las grabaciones de las llamadas."""
    monkeypatch.setattr(global_settings, "session_secret", "")
    monkeypatch.setattr(global_settings, "repository", "postgres")
    with pytest.raises(RuntimeError, match="BC_SESSION_SECRET"):
        create_app(deps, streaming_factory=None)


def test_real_mode_rejects_a_too_short_secret(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(global_settings, "session_secret", "x" * (MIN_SECRET_LENGTH - 1))
    monkeypatch.setattr(global_settings, "repository", "postgres")
    with pytest.raises(RuntimeError, match="BC_SESSION_SECRET"):
        create_app(deps, streaming_factory=None)


def test_in_development_the_secret_is_random_and_unguessable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin secreto configurado, desarrollo sigue funcionando, pero la firma no es ni constante ni
    derivable del nombre del repositorio."""
    monkeypatch.setattr(global_settings, "session_secret", "")
    monkeypatch.setattr(global_settings, "repository", "memory")
    primero, segundo = signing_secret(), signing_secret()
    assert primero != segundo
    assert len(primero) >= MIN_SECRET_LENGTH
    assert not primero.startswith("dev-") and "Repository" not in primero


def test_the_configured_secret_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    monkeypatch.setattr(global_settings, "repository", "postgres")
    assert signing_secret() == TEST_SECRET
