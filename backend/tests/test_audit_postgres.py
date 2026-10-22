"""Audit against a real PostgreSQL: triggers, stamps, fingerprints, append-only and erasure.

Needs `BC_TEST_DATABASE_URL` pointing to an **empty, disposable** test database: the module
drops its schema and migrates it from scratch with Alembic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from building_call.audit.install import (
    DELETED_CALL,
    INSTALLED_TRIGGERS_SQL,
    arguments,
    audited_tables,
)

URL = os.environ.get("BC_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="integration: needs BC_TEST_DATABASE_URL pointing to a test database"
)
ROOT = Path(__file__).parents[1]
APPEND_ONLY = "append-only"


def _alembic(*args: str) -> None:
    env = {**os.environ, "BC_DATABASE_URL": URL, "PYTHONUTF8": "1"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr[-2000:]


async def _empty() -> None:
    engine = create_async_engine(URL)
    async with engine.begin() as c:
        await c.execute(text("DROP SCHEMA public CASCADE"))
        await c.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="module")
def migrated() -> Iterator[None]:
    asyncio.run(_empty())
    _alembic("upgrade", "head")
    yield


@pytest.fixture
async def db(migrated: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(URL)
    yield engine
    await engine.dispose()


async def _as(db: AsyncEngine, actor: str | None, *statements: str) -> None:
    async with db.begin() as c:
        if actor is not None:
            await c.execute(
                text(
                    "SELECT set_config('fincall.actor', :a, true), "
                    "set_config('fincall.origin', 'console', true), "
                    "set_config('fincall.request', 'req-1', true)"
                ),
                {"a": actor},
            )
        for s in statements:
            await c.execute(text(s))


async def _changes(db: AsyncEngine, table: str, record_id: str) -> list[dict[str, Any]]:
    async with db.connect() as c:
        rows = await c.execute(
            text(
                "SELECT operation, actor, origin, request_id, before, after "
                "FROM audit_changes WHERE table_name = :t AND record_id = :r ORDER BY id"
            ),
            {"t": table, "r": record_id},
        )
        return [dict(r._mapping) for r in rows]


async def _one(db: AsyncEngine, sql: str, **params: Any) -> Any:
    async with db.connect() as c:
        return (await c.execute(text(sql), params)).first()


def _fingerprint(value: str) -> dict[str, str]:
    return {"fingerprint": hashlib.sha256(json.dumps(value).encode()).hexdigest()[:16]}


# ------------------------------------------------------------------------ changes and stamps


async def test_insert_update_and_delete_are_kept_with_who_when_and_what(db: AsyncEngine) -> None:
    await _as(db, "user:u-1", "INSERT INTO administrators (id, name) VALUES ('a-1', 'Fincas Sol')")
    await _as(db, "user:u-2", "UPDATE administrators SET name = 'Fincas Luna' WHERE id = 'a-1'")
    await _as(db, "user:u-3", "DELETE FROM administrators WHERE id = 'a-1'")
    insert, update, delete = await _changes(db, "administrators", "a-1")
    assert (insert["operation"], insert["actor"], insert["origin"], insert["request_id"]) == (
        "insert",
        "user:u-1",
        "console",
        "req-1",
    )
    assert insert["after"] == {"id": "a-1", "name": "Fincas Sol"}
    assert update["operation"] == "update" and update["actor"] == "user:u-2"
    assert (update["before"], update["after"]) == ({"name": "Fincas Sol"}, {"name": "Fincas Luna"})
    assert delete["operation"] == "delete" and delete["actor"] == "user:u-3"
    # The delete keeps the whole row, with who created it and who changed it last
    assert delete["before"]["name"] == "Fincas Luna"
    assert delete["before"]["audit_created_by"] == "user:u-1"
    assert delete["before"]["audit_updated_by"] == "user:u-2"


async def test_the_database_writes_the_stamps_and_they_cannot_be_forged(db: AsyncEngine) -> None:
    await _as(
        db,
        "user:real",
        "INSERT INTO administrators (id, name, audit_created_by, audit_updated_by) "
        "VALUES ('a-2', 'X', 'user:fake', 'user:fake')",
    )
    await _as(db, "user:other", "UPDATE administrators SET audit_created_by = 'user:fake' WHERE id = 'a-2'")
    row = await _one(db, "SELECT audit_created_by, audit_updated_by FROM administrators WHERE id = 'a-2'")
    assert tuple(row) == ("user:real", "user:real")


async def test_a_change_outside_the_application_is_recorded_as_the_database_role(
    db: AsyncEngine,
) -> None:
    await _as(db, None, "INSERT INTO administrators (id, name) VALUES ('a-3', 'Y')")
    (insert,) = await _changes(db, "administrators", "a-3")
    assert insert["actor"].startswith("db:")


async def test_personal_fields_are_kept_as_a_fingerprint_never_the_value(db: AsyncEngine) -> None:
    await _as(
        db,
        "agent:livekit",
        "INSERT INTO calls (id, started_at, caller_phone, transcript, state, needs_review) "
        "VALUES ('call-1', now(), '600111222', '[]', 'active', false)",
    )
    await _as(db, "user:u-1", "UPDATE calls SET caller_phone = '600999888' WHERE id = 'call-1'")
    insert, update = await _changes(db, "calls", "call-1")
    assert insert["after"]["caller_phone"] == _fingerprint("600111222")
    assert update["before"]["caller_phone"] == _fingerprint("600111222")
    log = json.dumps([insert, update])
    assert "600111222" not in log and "600999888" not in log


async def test_a_change_only_in_ignored_fields_is_neither_logged_nor_stamped(
    db: AsyncEngine,
) -> None:
    await _as(
        db,
        "user:u-1",
        "INSERT INTO users (id, username, role, active, access_state, must_change_password) "
        "VALUES ('u-9', 'ana', 'quality', true, 'granted', false)",
        "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at) "
        "VALUES ('s-1', 'u-9', 'h', now(), now() + interval '1 day')",
    )
    before = await _one(db, "SELECT audit_updated_at FROM sessions WHERE id = 's-1'")
    await _as(db, "user:u-9", "UPDATE sessions SET expires_at = expires_at + interval '1 day' WHERE id = 's-1'")
    assert [c["operation"] for c in await _changes(db, "sessions", "s-1")] == ["insert"]
    assert (await _one(db, "SELECT audit_updated_at FROM sessions WHERE id = 's-1'")) == before
    await _as(db, "user:u-1", "UPDATE sessions SET revoked_at = now() WHERE id = 's-1'")
    assert [c["operation"] for c in await _changes(db, "sessions", "s-1")] == ["insert", "update"]


async def test_the_live_call_state_is_stamped_but_not_logged(db: AsyncEngine) -> None:
    await _as(
        db,
        "agent:livekit",
        "INSERT INTO call_states (call_id, state, turn, version, finished, updated_at) "
        "VALUES ('call-1', '{}', 0, 1, false, now())",
    )
    assert await _changes(db, "call_states", "call-1") == []
    row = await _one(db, "SELECT audit_created_by FROM call_states WHERE call_id = 'call-1'")
    assert row[0] == "agent:livekit"


# --------------------------------------------------------------------------------- append-only


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_changes SET actor = 'nobody'",
        "DELETE FROM audit_changes",
        "TRUNCATE audit_changes",
        "TRUNCATE audit_access",
    ],
)
async def test_the_log_cannot_be_changed_or_deleted(db: AsyncEngine, statement: str) -> None:
    await _as(
        db, "user:u-1", "INSERT INTO administrators (id, name) VALUES ('a-4', 'Z') ON CONFLICT DO NOTHING"
    )
    with pytest.raises(Exception, match=APPEND_ONLY):
        await _as(db, "user:u-1", statement)


async def test_erasure_mode_does_not_open_the_log_to_anything_else(db: AsyncEngine) -> None:
    with pytest.raises(Exception, match=APPEND_ONLY):
        await _as(
            db,
            "user:u-1",
            "SELECT set_config('fincall.erasure', 'on', true)",
            "UPDATE audit_changes SET actor = 'nobody'",
        )
    with pytest.raises(Exception, match=APPEND_ONLY):
        await _as(
            db,
            "user:u-1",
            "SELECT set_config('fincall.erasure', 'on', true)",
            "DELETE FROM audit_changes",
        )


async def test_an_open_view_only_accepts_heartbeats_and_closing(db: AsyncEngine) -> None:
    await _as(
        db,
        "user:u-1",
        "INSERT INTO audit_access (id, action, resource, resource_id, actor, started_at) "
        "VALUES ('v-1', 'view', 'calls', 'call-1', 'user:u-1', now())",
    )
    await _as(db, "user:u-1", "UPDATE audit_access SET last_heartbeat_at = now() WHERE id = 'v-1'")
    with pytest.raises(Exception, match=APPEND_ONLY):
        await _as(db, "user:u-1", "UPDATE audit_access SET actor = 'user:other' WHERE id = 'v-1'")
    await _as(db, "user:u-1", "UPDATE audit_access SET ended_at = now() WHERE id = 'v-1'")
    with pytest.raises(Exception, match=APPEND_ONLY):
        await _as(db, "user:u-1", "UPDATE audit_access SET ended_at = now() WHERE id = 'v-1'")


# ---------------------------------------------------------------- consistency with the classes


async def test_the_installed_triggers_match_the_static_attributes(db: AsyncEngine) -> None:
    async with db.connect() as c:
        rows = (await c.execute(text(INSTALLED_TRIGGERS_SQL))).all()
    installed = {
        (r.table_name, r.trigger_name): tuple(r.arguments.split("\\000")[:-1]) for r in rows
    }
    for cls in audited_tables():
        table = cls.__tablename__
        assert installed.get((table, "audit_stamp")) == arguments(cls), table
        expected = arguments(cls) if cls.AUDIT_CHANGES else None
        assert installed.get((table, "audit_record")) == expected, table


async def test_the_deleted_call_sentinel_exists_and_is_hidden(db: AsyncEngine) -> None:
    from building_call.repos.postgres import PostgresRepository

    assert (await _one(db, "SELECT id FROM calls WHERE id = :id", id=DELETED_CALL)) is not None
    repo = PostgresRepository(URL)
    assert await repo.get_call(DELETED_CALL) is None
    assert DELETED_CALL not in {c.id for c in await repo.list_calls(limit=1000)}
    await repo.engine.dispose()


def test_the_migration_goes_down_and_up_again(migrated: None) -> None:
    _alembic("downgrade", "0011")
    _alembic("upgrade", "head")


# ---------------------------------------------------- from the request context to the database


async def test_the_repository_passes_the_actor_to_the_orm_and_to_core(db: AsyncEngine) -> None:
    from building_call.audit.context import AuditContext, audit_context, set_actor
    from building_call.domain.entities import Estate, Home
    from building_call.repos.postgres import PostgresRepository

    await _as(db, None, "INSERT INTO administrators (id, name) VALUES ('a-ctx', 'A')")
    repo = PostgresRepository(URL)
    with audit_context(AuditContext(request_id="r-9", origin="console")):
        set_actor("user:u-7")
        await repo.save_estate(  # ORM: merge
            Estate(
                id="f-ctx",
                administrator_id="a-ctx",
                address="Mayor 5",
                email="x@y.es",
                official_phone="910000000",
            )
        )
        await repo.save_home(Home(id="v-ctx", estate_id="f-ctx", floor="3", door="B"))
        set_actor("user:u-8")
        await repo.delete_home("v-ctx")  # Core: delete()
    await repo.engine.dispose()
    (estate,) = await _changes(db, "estates", "f-ctx")
    insert, delete = await _changes(db, "homes", "v-ctx")
    assert (estate["actor"], estate["request_id"]) == ("user:u-7", "r-9")
    assert insert["actor"] == "user:u-7"
    assert (delete["operation"], delete["actor"]) == ("delete", "user:u-8")


async def test_two_requests_at_once_do_not_mix_their_actor(db: AsyncEngine) -> None:
    from building_call.audit.context import AuditContext, audit_context, set_actor
    from building_call.domain.entities import Home
    from building_call.repos.postgres import PostgresRepository

    repo = PostgresRepository(URL)

    async def request(n: int) -> None:
        with audit_context(AuditContext(request_id=f"r-{n}", origin="console")):
            set_actor(f"user:p{n}")
            for i in range(3):
                await repo.save_home(Home(id=f"v-p{n}-{i}", estate_id="f-ctx", floor=str(i), door="A"))

    await asyncio.gather(*(request(n) for n in range(4)))
    await repo.engine.dispose()
    async with db.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT record_id, actor, request_id FROM audit_changes "
                    "WHERE table_name = 'homes' AND record_id LIKE 'v-p%'"
                )
            )
        ).all()
    assert len(rows) == 12
    for record_id, actor, request_id in rows:
        n = record_id.split("-")[1][1:]
        assert (actor, request_id) == (f"user:p{n}", f"r-{n}")


# ------------------------------------------------------------------------------ access log


async def test_views_in_postgres_open_extend_close_and_are_abandoned(db: AsyncEngine) -> None:
    from building_call.audit.access_log import AccessLog, PostgresAccessStore
    from building_call.audit.context import set_actor
    from building_call.repos.sql_models import CallSQL

    clock = [datetime(2026, 10, 22, 10, 0, tzinfo=UTC)]
    log = AccessLog(PostgresAccessStore(async_sessionmaker(db)), clock=lambda: clock[0])
    set_actor("user:pg-1")
    await log.read(CallSQL, "call-pg")
    view = await log.open_view("calls", "call-pg")
    abandoned = await log.open_view("calls", "call-pg-2")
    clock[0] += timedelta(seconds=30)
    assert await log.heartbeat(view)
    assert await log.heartbeat(abandoned)
    set_actor("user:other")
    assert not await log.close_view(view)
    set_actor("user:pg-1")
    clock[0] += timedelta(seconds=15)
    assert await log.close_view(view)
    assert not await log.close_view(view)
    clock[0] += timedelta(minutes=10)
    assert await log.close_abandoned() == 1
    rows = await log.store.list({"actor": "user:pg-1"})
    read, seen, left = sorted(rows, key=lambda r: (r["action"], r["resource_id"]))
    assert read["action"] == "read"
    assert (seen["ended_at"] - seen["started_at"]).total_seconds() == 45
    assert seen["detail"] == {"close": "closed"}
    assert (left["ended_at"] - left["started_at"]).total_seconds() == 30
    assert left["detail"] == {"close": "no_heartbeat"}


# ------------------------------------------------------------------------------- retention


async def test_retention_deletes_only_what_expired_and_only_from_the_purge(db: AsyncEngine) -> None:
    from building_call.audit.retention import purge_audit

    await _as(db, "user:u-1", "INSERT INTO administrators (id, name) VALUES ('a-ret', 'R')")
    now = datetime.now(UTC)
    # Nothing is two years old: nothing goes
    assert await purge_audit(async_sessionmaker(db), 730, now) == {"changes": 0, "accesses": 0}
    # With the clock ten years ahead, everything expired
    gone = await purge_audit(async_sessionmaker(db), 730, now + timedelta(days=3650))
    assert gone["changes"] > 0
    assert await _changes(db, "administrators", "a-ret") == []
    # And outside the purge it still cannot be deleted
    await _as(db, "user:u-1", "UPDATE administrators SET name = 'S' WHERE id = 'a-ret'")
    with pytest.raises(Exception, match=APPEND_ONLY):
        await _as(db, "user:u-1", "DELETE FROM audit_changes WHERE record_id = 'a-ret'")


# --------------------------------------------------------------------------------- erasure


async def _call_with_everything(db: AsyncEngine, call_id: str) -> None:
    await _as(
        db,
        "agent:livekit",
        "INSERT INTO administrators (id, name) VALUES ('a-er', 'A') ON CONFLICT DO NOTHING",
        "INSERT INTO estates (id, administrator_id, address, email, official_phone) "
        "VALUES ('f-er', 'a-er', 'Calle Borrada 1', 'f@x.es', '910000009') ON CONFLICT DO NOTHING",
        "INSERT INTO homes (id, estate_id, floor, door) VALUES ('h-er', 'f-er', '1', 'A') "
        "ON CONFLICT DO NOTHING",
        f"INSERT INTO calls (id, started_at, caller_phone, transcript, summary, state, needs_review)"
        f" VALUES ('{call_id}', now(), '600555444', '[{{\"role\": \"caller\", \"text\": \"hola\"}}]',"
        f" 'fuga en el baño', 'active', false)",
        f"INSERT INTO call_turns (id, call_id, turn, data) VALUES ('{call_id}:1', '{call_id}', 1,"
        f" '{{\"caller\": \"hay una fuga\"}}')",
        f"INSERT INTO call_decisions (id, call_id, turn, node, data) VALUES ('dec-er',"
        f" '{call_id}', 1, 'collect_request', '{{\"action\": \"x\"}}')",
        f"INSERT INTO audio_tracks (id, call_id, channel, object_key, mime, checksum, expires_at)"
        f" VALUES ('aud-er', '{call_id}', 'mix', '/nowhere/rec-er.wav', 'audio/wav', 'c',"
        f" now() + interval '14 days')",
        f"INSERT INTO incidents (id, call_id, estate_id, home_id, description, category, urgency,"
        f" state, created_at, work_type, suggestion) VALUES ('inc-er', '{call_id}', 'f-er',"
        f" 'h-er', 'fuga en el baño', 'plumbing', 'high', 'pending', now(), 'repair', 'avisar')",
        "INSERT INTO incident_history (id, incident_id, previous_state, new_state, note, action,"
        " detail) VALUES ('hist-er', 'inc-er', NULL, 'pending', 'dijo su dirección',"
        " 'note', '{\"notice\": \"aviso con datos\"}')",
        f"INSERT INTO audit_access (id, action, resource, resource_id, actor, started_at, ended_at,"
        f" detail) VALUES ('acc-er', 'read', 'calls', '{call_id}', 'user:u-1', now(), now(),"
        f" '{{\"part\": \"record\"}}')",
        f"INSERT INTO audit_events (id, user_id, action, resource, detail) VALUES ('ev-er',"
        f" NULL, 'mark_test_bank_case', 'call:{call_id}', '{{\"turn\": 1}}')",
    )
    await _as(db, "agent:livekit", f"UPDATE calls SET state = 'finished' WHERE id = '{call_id}'")


async def test_erasing_a_call_in_postgres(db: AsyncEngine) -> None:
    from building_call.audit.context import AuditContext, audit_context
    from building_call.audit.erasure import CallNotErasable, fingerprint
    from building_call.repos.postgres import PostgresRepository

    call_id = "call-erase"
    await _call_with_everything(db, call_id)
    repo = PostgresRepository(URL)
    with audit_context(AuditContext(actor="user:owner", origin="console", request_id="r-er")):
        files = await repo.erase_call(call_id)
        with pytest.raises(CallNotErasable):
            await repo.erase_call(call_id)
    await repo.engine.dispose()
    assert files == ["/nowhere/rec-er.wav"]

    # 1. The call and its children are gone
    for table, column in (
        ("calls", "id"),
        ("call_turns", "call_id"),
        ("call_decisions", "call_id"),
        ("audio_tracks", "call_id"),
    ):
        row = await _one(db, f"SELECT count(*) FROM {table} WHERE {column} = :id", id=call_id)  # noqa: S608
        assert row[0] == 0, table
    # 2. The incident stays, anonymised, with its work record
    incident = await _one(
        db,
        "SELECT call_id, description, suggestion, category, urgency, state FROM incidents "
        "WHERE id = :id",
        id="inc-er",
    )
    assert tuple(incident) == (DELETED_CALL, "", None, "plumbing", "high", "pending")
    history = await _one(db, "SELECT note, detail FROM incident_history WHERE id = :id", id="hist-er")
    assert tuple(history) == (None, {})
    # 3. Nothing in any log says which call it was or what it held
    async with db.connect() as c:
        dump = json.dumps(
            [
                [dict(r._mapping) for r in await c.execute(text(f"SELECT * FROM {t}"))]  # noqa: S608
                for t in ("audit_changes", "audit_access", "audit_events")
            ],
            default=str,
        )
    for secret in (call_id, "600555444", "hay una fuga", "dijo su dirección", "aviso con datos"):
        assert secret not in dump, secret
    access = await _one(db, "SELECT resource_id, detail FROM audit_access WHERE id = :id", id="acc-er")
    assert tuple(access) == (DELETED_CALL, {})
    # The incident's own history keeps its rows, without the reference to the call
    (insert, *_) = await _changes(db, "incidents", "inc-er")
    assert insert["after"]["call_id"] == DELETED_CALL and insert["after"]["category"] == "plumbing"
    # 4. One `erase` row: who, when, and the fingerprint of the id
    erase = await _one(
        db,
        "SELECT actor, origin, request_id, after FROM audit_changes WHERE operation = 'erase' "
        "ORDER BY id DESC LIMIT 1",
    )
    assert tuple(erase) == (
        "user:owner",
        "console",
        "r-er",
        {"call": {"fingerprint": fingerprint(call_id)}},
    )


async def test_a_call_in_progress_is_not_erased_in_postgres(db: AsyncEngine) -> None:
    from building_call.audit.erasure import CallNotErasable
    from building_call.repos.postgres import PostgresRepository

    await _as(
        db,
        "agent:livekit",
        "INSERT INTO calls (id, started_at, transcript, state, needs_review) "
        "VALUES ('call-live', now(), '[]', 'active', false)",
    )
    repo = PostgresRepository(URL)
    for call_id in ("call-live", DELETED_CALL, "does-not-exist"):
        with pytest.raises(CallNotErasable):
            await repo.erase_call(call_id)
    await repo.engine.dispose()
    assert (await _one(db, "SELECT count(*) FROM calls WHERE id = 'call-live'"))[0] == 1


# ------------------------------------------------------------------------------ end to end


async def test_a_change_from_the_console_is_recorded_as_the_person(
    db: AsyncEngine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from conftest import TEST_SECRET, FakeLLM
    from httpx import ASGITransport, AsyncClient

    from building_call.api.main import create_app
    from building_call.auth import hash_session_token
    from building_call.domain.operations import Role, User
    from building_call.graph.nodes import Dependencies
    from building_call.repos.operational import AccessSession
    from building_call.repos.postgres import PostgresRepository
    from building_call.settings import settings as global_settings

    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    repo = PostgresRepository(URL)
    await _as(db, None, "INSERT INTO administrators (id, name) VALUES ('a-http', 'A')")
    await repo.save_user(User(id="usr-http", username="owner-http", role=Role.owner, active=True))
    now = datetime.now(UTC)
    await repo.save_session(
        AccessSession("ses-http", "usr-http", hash_session_token("tok-http"), now, now + timedelta(hours=1))
    )
    app = create_app(Dependencies(repo=repo, llm=FakeLLM()), streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_session", "tok-http")
        r = await c.post(
            "/api/catalogs/estates",
            json={
                "administrator_id": "a-http",
                "address": "Calle de la Auditoría 1",
                "email": "f@x.es",
                "official_phone": "910000001",
            },
            headers={"X-Request-ID": "req-http"},
        )
        assert r.status_code == 201, r.text
        estate_id = r.json()["id"]
        await c.get("/api/operations/calls")
    await repo.engine.dispose()
    (insert,) = await _changes(db, "estates", estate_id)
    assert (insert["operation"], insert["actor"], insert["request_id"], insert["origin"]) == (
        "insert",
        "user:usr-http",
        "req-http",
        "console",
    )
    row = await _one(db, "SELECT audit_created_by FROM estates WHERE id = :id", id=estate_id)
    assert row[0] == "user:usr-http"
    listed = await _one(
        db, "SELECT actor FROM audit_access WHERE action = 'list' AND resource = 'calls' "
        "ORDER BY started_at DESC LIMIT 1"
    )
    assert listed[0] == "user:usr-http"
