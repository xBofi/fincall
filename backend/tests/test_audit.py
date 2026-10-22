"""Audit trail: who changed what (PostgreSQL triggers) and who looked at what (access log), and
the erasure of a call. Without a database: context, classes, generated SQL, access log, the
views API and the erasure in memory. Against PostgreSQL: `test_audit_postgres.py`.

Spec: `docs/superpowers/specs/2026-10-22-audit-trail-design.md`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import TEST_SECRET, make_session
from httpx import ASGITransport, AsyncClient

from building_call.api.main import create_app
from building_call.audit.access_log import AccessLog, MemoryAccessStore, NotAuditable
from building_call.audit.context import (
    NO_CONTEXT_ACTOR,
    AuditContext,
    audit_context,
    current_context,
    set_actor,
)
from building_call.audit.erasure import fingerprint
from building_call.audit.install import (
    DELETED_CALL,
    STAMPS,
    arguments,
    audited_tables,
    install_sql,
    trigger_sql,
)
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.repos.sql_models import (
    AUDIT_TABLES,
    AdministratorSQL,
    AudioTrackSQL,
    AuditedTable,
    Base,
    CallSQL,
    CallStateSQL,
    SessionSQL,
)
from building_call.settings import settings as global_settings

# ------------------------------------------------------------------------------- context


def test_without_context_the_actor_is_unknown_and_no_user_is_made_up() -> None:
    assert current_context().actor == NO_CONTEXT_ACTOR == "system:no_context"


async def test_the_actor_of_one_request_never_leaks_into_another() -> None:
    async def request(actor: str) -> str:
        with audit_context(AuditContext(request_id=actor, origin="console")):
            set_actor(actor)
            await asyncio.sleep(0)
            return current_context().actor

    seen = await asyncio.gather(request("user:a"), request("user:b"))
    assert seen == ["user:a", "user:b"]
    assert current_context().actor == NO_CONTEXT_ACTOR


async def test_a_task_started_from_the_request_inherits_the_actor() -> None:
    async def actor_in_task() -> str:
        return current_context().actor

    with audit_context(AuditContext(origin="console")):
        set_actor("user:a")
        child = await asyncio.create_task(actor_in_task())
    assert child == "user:a"


def test_set_actor_keeps_request_and_origin_and_there_is_no_ip() -> None:
    with audit_context(AuditContext(request_id="r-1", origin="console")):
        set_actor("user:a")
        ctx = current_context()
    assert (ctx.actor, ctx.request_id, ctx.origin) == ("user:a", "r-1", "console")
    assert not hasattr(ctx, "ip") and not hasattr(ctx, "user_agent")


# ------------------------------------------------------------------ base class and settings


def _mapped() -> list[type]:
    return [m.class_ for m in Base.registry.mappers]


def test_every_business_table_inherits_from_the_audited_class() -> None:
    loose = [
        c.__tablename__
        for c in _mapped()
        if not issubclass(c, AuditedTable) and c.__tablename__ not in AUDIT_TABLES
    ]
    assert loose == []


def test_the_audit_tables_do_not_audit_themselves() -> None:
    for c in _mapped():
        if c.__tablename__ in AUDIT_TABLES:
            assert not issubclass(c, AuditedTable), c.__tablename__


def test_every_audited_table_has_who_and_when_it_was_created_and_changed() -> None:
    for c in audited_tables():
        assert set(STAMPS) <= set(c.__table__.c.keys()), c.__tablename__


def test_the_static_fields_exist_in_their_table() -> None:
    for c in audited_tables():
        columns = set(c.__table__.c.keys())
        for group in (c.PERSONAL_FIELDS, c.SECRET_FIELDS, c.IGNORED_FIELDS):
            assert set(group) <= columns, c.__tablename__
        fingerprinted = {*c.PERSONAL_FIELDS, *c.SECRET_FIELDS}
        assert not fingerprinted & set(c.IGNORED_FIELDS), c.__tablename__


def test_the_call_is_personal_data_and_does_not_keep_the_callers_name() -> None:
    assert CallSQL.has_personal_data()
    assert {"caller_phone", "transcript", "summary"} <= set(CallSQL.PERSONAL_FIELDS)
    assert "declared_name" not in CallSQL.__table__.c


# ---------------------------------------------------------------- triggers from the classes


def test_the_trigger_arguments_come_from_the_static_attributes() -> None:
    assert arguments(SessionSQL) == ("id", "token_hash", "expires_at")
    assert arguments(CallSQL) == ("id", "caller_phone,transcript,summary", "")
    assert arguments(CallStateSQL)[0] == "call_id"


def test_every_table_is_stamped_and_logs_changes_only_if_its_class_asks() -> None:
    stamp, record = trigger_sql(CallSQL)
    assert "BEFORE INSERT OR UPDATE ON calls" in stamp
    assert "fincall_audit_stamp('id', 'caller_phone,transcript,summary', '')" in stamp
    assert "AFTER INSERT OR UPDATE OR DELETE ON calls" in record
    statements = " ".join(trigger_sql(CallStateSQL))
    assert "fincall_audit_stamp" in statements
    assert "DROP TRIGGER IF EXISTS audit_record ON call_states" in statements


def test_install_covers_every_audited_table_and_guards_the_log() -> None:
    sql = "\n".join(install_sql())
    for c in audited_tables():
        assert f" ON {c.__tablename__} " in sql
    for table in ("audit_changes", "audit_access"):
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql
        assert f"BEFORE TRUNCATE ON {table}" in sql


def test_the_python_fingerprint_matches_the_database_one_for_scalars() -> None:
    expected = hashlib.sha256(json.dumps("600111222").encode()).hexdigest()[:16]
    assert fingerprint("600111222") == expected


# ------------------------------------------------------------------------------ access log


def _log() -> tuple[AccessLog, list[datetime]]:
    clock = [datetime(2026, 10, 22, 10, 0, tzinfo=UTC)]
    return AccessLog(MemoryAccessStore(), clock=lambda: clock[0]), clock


async def _rows(log: AccessLog) -> list[dict]:
    return await log.store.list({}, newest_first=False)


async def test_reading_personal_data_is_logged_with_who_and_when_but_no_ip() -> None:
    log, _ = _log()
    with audit_context(AuditContext(request_id="r-1")):
        set_actor("user:u-1", "console")
        await log.read(CallSQL, "call-1")
    (row,) = await _rows(log)
    assert (row["action"], row["resource"], row["resource_id"]) == ("read", "calls", "call-1")
    assert (row["actor"], row["origin"], row["request_id"]) == ("user:u-1", "console", "r-1")
    assert row["started_at"] == row["ended_at"]
    assert "ip" not in row and "user_agent" not in row


async def test_reading_something_without_personal_data_is_not_logged() -> None:
    log, _ = _log()
    await log.read(AdministratorSQL, "adm-1")
    assert await _rows(log) == []


async def test_a_list_logs_its_filter_and_how_many_rows_it_returned() -> None:
    log, _ = _log()
    await log.list(CallSQL, {"state": "active", "estate_id": None}, rows=7)
    (row,) = await _rows(log)
    assert row["action"] == "list" and row["resource_id"] is None
    assert row["detail"] == {"filter": {"state": "active"}, "rows": 7}


async def test_playing_and_downloading_a_file_are_logged() -> None:
    log, _ = _log()
    await log.play(AudioTrackSQL, "track-1", bytes=48000, call_id="call-1")
    await log.download("bank_cases", None, format="jsonl", rows=12)
    play, download = await _rows(log)
    assert play["action"] == "play" and play["detail"] == {"bytes": 48000, "call_id": "call-1"}
    assert (download["action"], download["resource"]) == ("download", "bank_cases")


async def test_a_view_lasts_from_opening_to_closing() -> None:
    log, clock = _log()
    set_actor("user:u-1")
    view = await log.open_view("calls", "call-1")
    clock[0] += timedelta(seconds=30)
    assert await log.heartbeat(view)
    clock[0] += timedelta(seconds=12)
    assert await log.close_view(view)
    (row,) = await _rows(log)
    assert row["action"] == "view"
    assert (row["ended_at"] - row["started_at"]).total_seconds() == 42
    assert row["detail"] == {"close": "closed"}
    # Closed: no more heartbeats nor another close
    assert not await log.heartbeat(view)
    assert not await log.close_view(view)


async def test_nobody_can_close_or_extend_someone_elses_view() -> None:
    log, _ = _log()
    set_actor("user:u-1")
    view = await log.open_view("calls", "call-1")
    set_actor("user:u-2")
    assert not await log.heartbeat(view)
    assert not await log.close_view(view)


async def test_an_abandoned_view_ends_at_its_last_heartbeat() -> None:
    log, clock = _log()
    set_actor("user:u-1")
    view = await log.open_view("calls", "call-1")
    clock[0] += timedelta(seconds=30)
    await log.heartbeat(view)
    clock[0] += timedelta(seconds=600)  # the browser closed without saying
    assert await log.close_abandoned() == 1
    (row,) = await _rows(log)
    assert (row["ended_at"] - row["started_at"]).total_seconds() == 30
    assert row["detail"]["close"] == "no_heartbeat"


async def test_views_are_only_opened_for_tables_with_personal_data() -> None:
    log, _ = _log()
    with pytest.raises(NotAuditable):
        await log.open_view("administrators", "adm-1")
    with pytest.raises(NotAuditable):
        await log.open_view("does_not_exist", "x")


async def test_erasing_a_call_leaves_that_someone_looked_not_at_which_call() -> None:
    log, _ = _log()
    await log.read(CallSQL, "call-1", part="record")
    await log.play(AudioTrackSQL, "track-1", call_id="call-1")
    await log.read(CallSQL, "call-2")
    await log.erase("call-1")
    first, second, other = await _rows(log)
    assert (first["resource_id"], first["detail"]) == (DELETED_CALL, {})
    assert (second["resource_id"], second["detail"]) == (DELETED_CALL, {})
    assert other["resource_id"] == "call-2"


# ------------------------------------------------------------------------------ HTTP API


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _sign_in(client: AsyncClient, repo: MemoryRepository, role: str) -> None:
    client.cookies.set("bc_session", make_session(repo, role))


def _access_log(client: AsyncClient) -> AccessLog:
    return client._transport.app.state.access_log  # type: ignore[attr-defined]


async def test_without_a_session_no_view_is_opened(client: AsyncClient) -> None:
    r = await client.post("/api/audit/views", json={"resource": "calls", "resource_id": "x"})
    assert r.status_code == 401


async def test_the_console_opens_extends_and_closes_the_view_of_a_record(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    _sign_in(client, repo, "quality")
    r = await client.post(
        "/api/audit/views",
        json={"resource": "calls", "resource_id": "call-1"},
        headers={"X-Request-ID": "test-request"},
    )
    assert r.status_code == 201
    view = r.json()
    assert view["heartbeat_s"] == 30
    assert (await client.post(f"/api/audit/views/{view['id']}/heartbeat")).status_code == 204
    assert (await client.post(f"/api/audit/views/{view['id']}/end")).status_code == 204
    # `sendBeacon` may repeat it when closing: the second close does not fail
    assert (await client.post(f"/api/audit/views/{view['id']}/end")).status_code == 204
    assert (await client.post(f"/api/audit/views/{view['id']}/heartbeat")).status_code == 404
    (row,) = await _access_log(client).store.list({"action": "view"})
    assert row["request_id"] == "test-request"
    assert row["actor"].startswith("user:") and row["ended_at"] is not None


async def test_no_view_is_opened_for_what_has_no_personal_data(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    _sign_in(client, repo, "quality")
    r = await client.post(
        "/api/audit/views", json={"resource": "administrators", "resource_id": "adm-1"}
    )
    assert r.status_code == 422


async def test_nobody_can_query_the_log_from_the_console(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    _sign_in(client, repo, "owner")
    for path in ("/api/audit/changes", "/api/audit/access", "/api/operations/audit"):
        assert (await client.get(path)).status_code in (404, 405), path


# --------------------------------------------------------------------------- erasure


FULL_CALL = [
    "Vivo en la calle Mayor número 12",
    "sí",
    "tercero B",
    "Hay una fuga de agua en el baño y está inundando el suelo",
    "sí, correcto",
]


async def _finished_call(client: AsyncClient, repo: MemoryRepository) -> str:
    _sign_in(client, repo, "owner")
    r = await client.post("/api/calls", json={"caller_phone": "+34600111222"})
    call_id = r.json()["call_id"]
    for text in FULL_CALL:
        await client.post(f"/api/calls/{call_id}/messages", json={"text": text})
    return str(call_id)


async def test_only_the_owner_erases_a_call(client: AsyncClient, repo: MemoryRepository) -> None:
    call_id = await _finished_call(client, repo)
    for role in ("quality", "administrator"):
        _sign_in(client, repo, role)
        r = await client.post(f"/api/operations/calls/{call_id}/erase")
        assert r.status_code == 403, role
    assert await repo.get_call(call_id) is not None


async def test_erasing_a_call_removes_it_and_anonymises_its_incidents(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    call_id = await _finished_call(client, repo)
    (incident,) = await repo.list_incidents()
    assert incident.call_id == call_id
    _sign_in(client, repo, "quality")
    case = {"turn": 1, "expected": {"intent": "continue", "address": "mayor 12"}}
    assert (await client.post(f"/api/operations/calls/{call_id}/cases", json=case)).status_code == 201
    _sign_in(client, repo, "owner")
    await client.get(f"/api/operations/calls/{call_id}")

    assert (await client.post(f"/api/operations/calls/{call_id}/erase")).status_code == 204

    assert await repo.get_call(call_id) is None
    assert await repo.list_turns(call_id) == [] and await repo.list_decisions(call_id) == []
    assert await repo.list_test_bank_cases() == []
    assert await repo.call_state_store.get(call_id) is None
    (kept,) = await repo.list_incidents()
    assert (kept.id, kept.call_id, kept.description, kept.suggestion) == (
        incident.id,
        DELETED_CALL,
        "",
        None,
    )
    # The work record stays
    assert (kept.category, kept.urgency, kept.state) == (
        incident.category,
        incident.urgency,
        incident.state,
    )
    assert (await client.get(f"/api/operations/calls/{call_id}")).status_code == 404
    rows = await _access_log(client).store.list({})
    assert rows and not any(r["resource_id"] == call_id for r in rows)
    assert not any(call_id in json.dumps(e, default=str) for e in await repo.list_audit())
    # The console shows "Llamada eliminada": the inbox no longer brings that call
    detail = (await client.get(f"/api/inbox/incidents/{kept.id}")).json()
    assert detail["incident"]["call_id"] == DELETED_CALL and detail["call"] is None


async def test_a_call_in_progress_or_unknown_is_not_erased(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    _sign_in(client, repo, "owner")
    call_id = (await client.post("/api/calls", json={})).json()["call_id"]
    assert (await client.post(f"/api/operations/calls/{call_id}/erase")).status_code == 409
    assert (await client.post("/api/operations/calls/nope/erase")).status_code == 404
    r = await client.post(f"/api/operations/calls/{DELETED_CALL}/erase")
    assert r.status_code == 404
