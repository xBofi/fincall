"""Arreglos de la revisión de seguridad del 2026-10-22 (los de severidad media y baja)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import make_session

from building_call.access import SESSION_MAX_DAYS, Access
from building_call.api.main import create_app
from building_call.auth import hash_session_token
from building_call.domain.operations import AccessState, Role, User
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.settings import Settings
from building_call.settings import settings as global_settings


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")


async def test_a_huge_text_does_not_reach_the_model(deps: Dependencies) -> None:
    app = create_app(deps, streaming_factory=None)
    async with _client(app) as c:
        c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
        cid = (await c.post("/api/calls", json={})).json()["call_id"]
        huge = "palabra " * 5000
        assert (
            await c.post(f"/api/calls/{cid}/partial", json={"text": huge})
        ).status_code == 422
        assert (
            await c.post(f"/api/calls/{cid}/messages", json={"text": huge})
        ).status_code == 422


async def test_a_short_agent_secret_gives_no_access(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(global_settings, "agent_secret", "agent")
    app = create_app(deps, streaming_factory=None)
    async with _client(app) as c:
        r = await c.post("/api/calls", json={}, headers={"Authorization": "Bearer agente"})
        assert r.status_code == 401


async def test_livekit_with_development_keys_is_not_exposed(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {
        "livekit_url": "ws://localhost:7880",
        "livekit_api_key": "devkey",
        "livekit_api_secret": "secret",
        "livekit_console_url": "/",
    }.items():
        monkeypatch.setattr(global_settings, k, v)
    app = create_app(deps, streaming_factory=None)
    async with _client(app) as c:
        c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"call_id": "llam-1"})
        assert r.status_code == 503


def test_the_cookie_is_secure_if_the_public_console_is_https() -> None:
    from starlette.requests import Request

    ask = Request(
        {
            "type": "http",
            "headers": [],
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "path": "/",
        }
    )
    local = Access(MemoryRepository(), Settings(_env_file=None))  # type: ignore[arg-type]
    public_url = Access(
        MemoryRepository(),  # type: ignore[arg-type]
        Settings(_env_file=None, console_url="https://consola.ejemplo.es"),
    )
    assert local._secure(ask) is False
    assert public_url._secure(ask) is True


async def test_the_session_expires_even_if_still_used(repo: MemoryRepository) -> None:
    token = make_session(repo)
    session = repo.sessions[hash_session_token(token)]
    session.created_at = datetime.now(UTC) - timedelta(days=SESSION_MAX_DAYS + 1)
    session.expires_at = datetime.now(UTC) + timedelta(days=10)
    from starlette.requests import Request

    ask = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"bc_session={token}".encode())],
            "scheme": "http",
            "server": ("localhost", 80),
            "path": "/",
        }
    )
    access = Access(repo, Settings(_env_file=None))  # type: ignore[arg-type]
    assert await access.person_from_request(ask) is None


async def test_a_denied_person_cannot_retry_the_same_day(deps: Dependencies) -> None:
    repo: MemoryRepository = deps.repo  # type: ignore[assignment]
    token = make_session(repo, "quality")
    user = repo.users["usr-quality"]
    repo.users["usr-quality"] = User(
        **{
            **user.model_dump(),
            "role": Role.quality,
            "access_state": AccessState.denied,
            "requested_at": datetime.now(UTC) - timedelta(hours=2),
        }
    )
    app = create_app(deps, streaming_factory=None)
    async with _client(app) as c:
        c.cookies.set("bc_session", token)
        assert (await c.post("/api/auth/submit_request")).status_code == 429
