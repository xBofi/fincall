"""Bandeja del administrador: incidencias que esperan una decisión suya y sus acciones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import TEST_SECRET, make_session

from building_call.api.main import create_app
from building_call.domain.entities import Call, Incident
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.settings import settings as global_settings

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _seed(repo: MemoryRepository) -> None:
    """Tres incidencias: una del administrador (siniestro del portero), una asignada al
    fontanero y una pendiente sin especialista en otra finca."""
    await repo.save_call(
        Call(
            id="llam-portero",
            started_at=NOW,
            estate_id="estate-main-12",
            home_id="estate-main-12-u-3B",
            transcript=[
                {"role": "bot", "text": "Hi, how can I help you?"},
                {"role": "caller", "text": "Someone smashed the intercom"},
            ],
            summary="Someone vandalized the intercom at the entrance.",
            result="incident_created",
            review_reason="work_type:insurance_claim",
            state="finished",
        )
    )
    await repo.save_incident(
        Incident(
            id="inc-portero",
            call_id="llam-portero",
            estate_id="estate-main-12",
            home_id="estate-main-12-u-3B",
            description="Someone smashed the intercom",
            category="electrical",
            specialist_id=None,
            urgency="medium",
            created_at=NOW - timedelta(hours=2),
            equipment_id="eq-main12-intercom",
            work_type="insurance_claim",
            suggestion="It may be an insurance claim. Equipment: intercom.",
        )
    )
    await repo.save_call(
        Call(id="llam-grifo", started_at=NOW, estate_id="estate-main-12", state="finished")
    )
    await repo.save_incident(
        Incident(
            id="inc-grifo",
            call_id="llam-grifo",
            estate_id="estate-main-12",
            home_id="estate-main-12-u-1A",
            description="A faucet is leaking in the meter room",
            category="plumbing",
            specialist_id="spc-plumber",
            urgency="high",
            state="assigned",
            created_at=NOW - timedelta(hours=1),
        )
    )
    await repo.save_call(
        Call(
            id="llam-limpieza",
            started_at=NOW,
            estate_id="estate-elm-5",
            review_reason="no_specialist_for_category",
            state="finished",
        )
    )
    await repo.save_incident(
        Incident(
            id="inc-limpieza",
            call_id="llam-limpieza",
            estate_id="estate-elm-5",
            home_id="estate-elm-5-1-1A",
            description="There's trash at the entrance",
            category="cleaning",
            specialist_id=None,
            urgency="emergency",
            created_at=NOW,
        )
    )


async def _log_in(client: AsyncClient, repo: MemoryRepository, role: str) -> None:
    await _seed(repo)
    client.cookies.set("bc_session", make_session(repo, role))


# ------------------------------------------------------------------------------------- permisos


async def test_without_session_the_inbox_returns_401(client: AsyncClient) -> None:
    assert (await client.get("/api/inbox/incidents")).status_code == 401
    assert (await client.get("/api/inbox/incidents/inc-portero")).status_code == 401
    r = await client.post("/api/inbox/incidents/inc-portero/note", json={"text": "x"})
    assert r.status_code == 401


async def test_quality_only_reads(client: AsyncClient, repo: MemoryRepository) -> None:
    await _log_in(client, repo, "quality")
    assert (await client.get("/api/inbox/incidents")).status_code == 200
    assert (await client.get("/api/inbox/incidents/inc-portero")).status_code == 200
    base = "/api/inbox/incidents/inc-portero"
    assert (
        await client.post(f"{base}/assign", json={"specialist_id": "spc-intercoms"})
    ).status_code == 403
    assert (await client.post(f"{base}/state", json={"state": "in_progress"})).status_code == 403
    assert (await client.post(f"{base}/note", json={"text": "Visto"})).status_code == 403
    assert repo.incidents["inc-portero"].state == "pending"
    assert await repo.list_incident_history("inc-portero") == []


@pytest.mark.parametrize("role", ["owner", "administrator"])
async def test_owner_and_administrator_act(
    client: AsyncClient, repo: MemoryRepository, role: str
) -> None:
    await _log_in(client, repo, role)
    r = await client.post("/api/inbox/incidents/inc-portero/note", json={"text": "Visto"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------------------- lista


async def test_list_puts_administrator_pending_first(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _log_in(client, repo, "administrator")
    rows = (await client.get("/api/inbox/incidents")).json()
    ids = [f["id"] for f in rows]
    # Pendientes del administrador primero (por urgencia), después el resto
    assert ids == ["inc-limpieza", "inc-portero", "inc-grifo"]
    intercom = rows[1]
    assert intercom["pending_administrator"] is True
    assert intercom["estate_address"] == "12 Main Street"
    assert intercom["home"] == "Apt 3B"
    assert intercom["review_reason"] == "work_type:insurance_claim"
    assert intercom["suggestion"].startswith("It may be an insurance claim")
    assert rows[2]["specialist_name"] == "Smith Plumbing"
    assert rows[2]["pending_administrator"] is False


async def test_list_filters(client: AsyncClient, repo: MemoryRepository) -> None:
    await _log_in(client, repo, "owner")

    async def ids(query: str) -> list[str]:
        return [f["id"] for f in (await client.get(f"/api/inbox/incidents?{query}")).json()]

    assert await ids("pending_administrator_only=true") == ["inc-limpieza", "inc-portero"]
    assert await ids("state=assigned") == ["inc-grifo"]
    assert await ids("estate_id=estate-elm-5") == ["inc-limpieza"]
    assert await ids("urgency=high") == ["inc-grifo"]
    assert await ids("estate_id=estate-main-12&pending_administrator_only=true") == ["inc-portero"]
    assert (await client.get("/api/inbox/incidents?state=inventado")).status_code == 422


# -------------------------------------------------------------------------------------- detalle


async def test_detail_brings_call_equipment_and_specialists(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _log_in(client, repo, "quality")
    d = (await client.get("/api/inbox/incidents/inc-portero")).json()
    assert d["incident"]["suggestion"].startswith("It may be an insurance claim")
    assert d["call"]["summary"].startswith("Someone vandalized")
    assert d["call"]["review_reason"] == "work_type:insurance_claim"
    assert d["call"]["transcript"][1]["role"] == "caller"
    assert d["equipment"]["description"] == "intercom Fermax Citymax (main entrance)"
    assert d["equipment"]["maintainer"]["name"] == "Eastside Intercoms"
    assert d["estate"]["address"] == "12 Main Street"
    # Para el selector de asignación: solo los especialistas de su administrador
    assert {e["id"] for e in d["specialists"]} >= {"spc-intercoms", "spc-plumber"}
    assert d["history"] == []
    log = client._transport.app.state.access_log  # type: ignore[attr-defined]
    assert any(
        (a["action"], a["resource"], a["resource_id"]) == ("read", "incidents", "inc-portero")
        for a in await log.store.list({})
    )
    assert (await client.get("/api/inbox/incidents/inc-nada")).status_code == 404


# -------------------------------------------------------------------------------------- acciones


async def test_assign_leaves_the_notice_pending_delivery(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _log_in(client, repo, "administrator")
    r = await client.post(
        "/api/inbox/incidents/inc-portero/assign",
        json={"specialist_id": "spc-intercoms", "note": "Have the maintainer look at it"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["incident"]["state"] == "assigned"
    assert body["incident"]["specialist_id"] == "spc-intercoms"
    assert body["notice"] == "pending_send"
    assert repo.incidents["inc-portero"].specialist_id == "spc-intercoms"

    history = await repo.list_incident_history("inc-portero")
    assert len(history) == 1
    h = history[0]
    assert h.action == "assign"
    assert (h.previous_state, h.new_state) == ("pending", "assigned")
    assert h.user_id == "usr-administrator"
    assert h.note == "Have the maintainer look at it"
    assert h.detail["specialist_id"] == "spc-intercoms"
    assert h.detail["notice"] == "pending_send"
    audit = await repo.list_audit()
    assert any(
        a["action"] == "assign_incident" and a["detail"]["notice"] == "pending_send" for a in audit
    )
    # El detalle ya muestra el historial
    d = (await client.get("/api/inbox/incidents/inc-portero")).json()
    assert d["history"][0]["action"] == "assign"
    assert d["history"][0]["user_name"] == "administrator"
    assert d["specialist"]["name"] == "Eastside Intercoms"
    # Ya no está pendiente del administrador
    rows = (await client.get("/api/inbox/incidents?pending_administrator_only=true")).json()
    assert [f["id"] for f in rows] == ["inc-limpieza"]


async def test_assign_rejects_foreign_specialists(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _log_in(client, repo, "owner")
    from building_call.domain.entities import Administrator, Specialist

    repo.administrators["adm-otro"] = Administrator(id="adm-otro", name="Otra SL")
    repo.specialists["esp-ajeno"] = Specialist(
        id="esp-ajeno",
        administrator_id="adm-otro",
        name="Ajeno",
        main_category="plumbing",
        contact_channel="email",
        target="ajeno@demo.test",
    )
    base = "/api/inbox/incidents/inc-portero/assign"
    assert (await client.post(base, json={"specialist_id": "esp-ajeno"})).status_code == 422
    assert (await client.post(base, json={"specialist_id": "esp-nada"})).status_code == 422
    assert repo.incidents["inc-portero"].specialist_id is None
    assert await repo.list_incident_history("inc-portero") == []


async def test_change_status_is_kept_in_history_and_audit(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _log_in(client, repo, "owner")
    base = "/api/inbox/incidents/inc-grifo/state"
    for state in ("in_progress", "awaiting_information", "resolved", "closed"):
        r = await client.post(base, json={"state": state})
        assert r.status_code == 200, r.text
        assert r.json()["incident"]["state"] == state
    history = await repo.list_incident_history("inc-grifo")
    assert [(h.previous_state, h.new_state) for h in history] == [
        ("assigned", "in_progress"),
        ("in_progress", "awaiting_information"),
        ("awaiting_information", "resolved"),
        ("resolved", "closed"),
    ]
    assert all(h.action == "change_state" for h in history)
    actions = [a["action"] for a in await repo.list_audit()]
    assert actions.count("change_incident_state") == 4
    # Cerrada es final: ni cambia de estado, ni se asigna
    assert (await client.post(base, json={"state": "in_progress"})).status_code == 409
    r = await client.post(
        "/api/inbox/incidents/inc-grifo/assign", json={"specialist_id": "spc-plumber"}
    )
    assert r.status_code == 409


async def test_change_status_validates_the_target(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _log_in(client, repo, "owner")
    base = "/api/inbox/incidents/inc-portero/state"
    # "asignada" solo se alcanza asignando; "pendiente" no es un destino manual
    assert (await client.post(base, json={"state": "assigned"})).status_code == 422
    assert (await client.post(base, json={"state": "pending"})).status_code == 422
    assert (await client.post(base, json={"state": "inventado"})).status_code == 422
    r = await client.post("/api/inbox/incidents/inc-nada/state", json={"state": "closed"})
    assert r.status_code == 404


async def test_note_does_not_change_the_status(client: AsyncClient, repo: MemoryRepository) -> None:
    await _log_in(client, repo, "administrator")
    base = "/api/inbox/incidents/inc-portero/note"
    assert (await client.post(base, json={"text": "   "})).status_code == 422
    r = await client.post(base, json={"text": "I'll call the insurance tomorrow"})
    assert r.status_code == 200
    assert r.json()["incident"]["state"] == "pending"
    (h,) = await repo.list_incident_history("inc-portero")
    assert h.action == "note" and h.note == "I'll call the insurance tomorrow"
    assert h.previous_state == h.new_state == "pending"
    assert any(a["action"] == "incident_note" for a in await repo.list_audit())


# ---------------------------------------------------------------------------------- concurrencia


async def test_two_decisions_at_once_the_second_gets_409(
    client: AsyncClient, repo: MemoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dos administradores abren la misma incidencia pendiente; mientras el segundo decide, el
    primero ya la ha asignado. La segunda decisión no pisa la primera ni deja historial."""
    await _log_in(client, repo, "administrator")
    old_view = repo.incidents["inc-portero"]
    r = await client.post(
        "/api/inbox/incidents/inc-portero/assign", json={"specialist_id": "spc-intercoms"}
    )
    assert r.status_code == 200

    async def get_old(incident_id: str) -> Incident | None:
        return old_view if incident_id == "inc-portero" else None

    monkeypatch.setattr(repo, "get_incident", get_old)
    base = "/api/inbox/incidents/inc-portero"
    r = await client.post(f"{base}/assign", json={"specialist_id": "spc-plumber"})
    assert r.status_code == 409
    assert (await client.post(f"{base}/state", json={"state": "in_progress"})).status_code == 409
    assert repo.incidents["inc-portero"].specialist_id == "spc-intercoms"
    assert repo.incidents["inc-portero"].state == "assigned"
    assert [h.action for h in await repo.list_incident_history("inc-portero")] == ["assign"]
    actions = [a["action"] for a in await repo.list_audit()]
    assert actions.count("assign_incident") == 1
    assert "change_incident_state" not in actions
