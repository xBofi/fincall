"""El estado vivo de la llamada sobrevive a un reinicio de la API (y admite varias instancias).

El reinicio se simula con dos `CallManager` que comparten el mismo almacén: el primero empieza
la llamada y el segundo, nuevo y sin nada en memoria, la continúa.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import pytest
from en_helpers import FakeLLM
from sqlalchemy.dialects import postgresql

from building_call.audit.install import STAMPS
from building_call.graph.nodes import Dependencies
from building_call.repos.call_state import (
    MemoryStateStore,
    PostgresStateStore,
    purge_statement,
    save_statement,
)
from building_call.repos.memory import MemoryRepository
from building_call.repos.sql_models import CallStateSQL
from building_call.services import CallManager, CallNotFound

TURNS = ["I live at number 12 Main Street", "yes", "third floor, B", "My name is Ann"]


def _repo() -> MemoryRepository:
    r = MemoryRepository()
    r.administrator_config["adm-demo"] = r.administrator_config["adm-demo"].model_copy(
        update={"ask_detail": False}
    )
    return r


async def _continue(manager: CallManager, call_id: str, turns: list[str]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for text in turns:
        state = await manager.message(call_id, text)
    return state


@pytest.mark.parametrize("serialize", [False, True], ids=["references", "json"])
async def test_the_call_continues_in_a_new_manager(serialize: bool) -> None:
    """Con `serializar=True` el estado hace el mismo viaje que en PostgreSQL (JSONB)."""
    store = MemoryStateStore(serialize=serialize)
    repo_a, repo_b = _repo(), _repo()
    before = CallManager(Dependencies(repo=repo_a, llm=FakeLLM()), store=store)
    start = await before.begin()
    call_id = start["call_id"]
    await _continue(before, call_id, TURNS[:2])

    after = CallManager(Dependencies(repo=repo_b, llm=FakeLLM()), store=store)
    state = await _continue(after, call_id, [*TURNS[2:], "The kitchen faucet is leaking", "yes"])

    # La misma llamada sin pasos repetidos: los datos del primer proceso siguen ahí
    assert state["estate_id"] == "estate-main-12"
    assert state["home_label"]
    assert state["turn"] == 6
    assert state["result"] == "incident_created"
    texts = [h["text"] for h in state["history"] if h["role"] == "caller"]
    assert texts[:2] == TURNS[:2]
    # El gestor nuevo también sabe de ella sin haberla empezado
    assert (await after.get(call_id))["finished"] is True


async def test_delivery_recorded_before_restart_is_used_after() -> None:
    store = MemoryStateStore(serialize=True)
    before = CallManager(Dependencies(repo=_repo(), llm=FakeLLM()), store=store)
    state = await before.begin()
    text = state["last_bot_message"]
    await before.record_delivery(state["call_id"], state["turn"], "interrupted", 0.1, text[:10])
    after = CallManager(Dependencies(repo=_repo(), llm=FakeLLM()), store=store)
    recovered = await after.get(state["call_id"])
    assert recovered["last_delivery"]["state"] == "interrupted"
    assert recovered["last_delivery"]["delivered_text"] == text[:10]


async def test_the_manager_uses_the_repository_store() -> None:
    repo = _repo()
    before = CallManager(Dependencies(repo=repo, llm=FakeLLM()))
    state = await before.begin()
    after = CallManager(Dependencies(repo=repo, llm=FakeLLM()))
    assert (await after.get(state["call_id"]))["turn"] == 0
    assert isinstance(repo.call_state_store, MemoryStateStore)


async def test_unknown_call_is_not_found() -> None:
    manager = CallManager(Dependencies(repo=_repo(), llm=FakeLLM()))
    with pytest.raises(CallNotFound):
        await manager.get("llam-inexistente")
    with pytest.raises(CallNotFound):
        manager.get_local("llam-inexistente")


class CountingStore(MemoryStateStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.writes = 0

    async def get(self, call_id: str) -> Any:
        self.reads += 1
        return await super().get(call_id)

    async def save(self, state: Any, now: float) -> None:
        self.writes += 1
        await super().save(state, now)


async def test_a_turn_does_at_most_one_read_and_one_write() -> None:
    store = CountingStore()
    manager = CallManager(Dependencies(repo=_repo(), llm=FakeLLM()), store=store)
    state = await manager.begin()
    assert (store.reads, store.writes) == (0, 1)
    await manager.message(state["call_id"], TURNS[0])
    assert (store.reads, store.writes) == (1, 2)


async def test_discard_removes_the_state() -> None:
    store = MemoryStateStore()
    await store.save({"call_id": "llam-1", "turn": 0}, 0.0)  # type: ignore[typeddict-item]
    await store.discard("llam-1")
    assert await store.get("llam-1") is None


async def test_purge_from_another_process_respects_the_retention() -> None:
    store = MemoryStateStore()
    await store.save({"call_id": "end", "finished": True}, 0.0)  # type: ignore[typeddict-item]
    await store.save({"call_id": "alive", "finished": False}, 0.0)  # type: ignore[typeddict-item]
    rf, ra = CallManager.FINISHED_RETENTION_S, CallManager.ABANDONED_RETENTION_S
    assert await store.purge(rf + 1, rf, ra) == ["end"]
    assert await store.get("alive") is not None
    assert await store.purge(ra + 1, rf, ra) == ["alive"]


async def test_the_in_memory_store_costs_microseconds() -> None:
    """Presupuesto de latencia: guardar + leer el estado de una llamada larga, muy por debajo del
    milisegundo en memoria (el turno con LLM ronda 1 s; esto no debe notarse)."""
    manager = CallManager(Dependencies(repo=_repo(), llm=FakeLLM()))
    state = await _continue(manager, (await manager.begin())["call_id"], TURNS)
    store = MemoryStateStore()
    n = 2000
    t0 = perf_counter()
    for i in range(n):
        await store.save(state, float(i))
        await store.get(state["call_id"])
    mean_ms = (perf_counter() - t0) * 1000 / n
    assert mean_ms < 0.5, f"{mean_ms:.3f} ms por turno"


@pytest.mark.parametrize("serialize", [False, True], ids=["references", "json"])
async def test_a_previous_turn_state_does_not_overwrite_the_new_one(serialize: bool) -> None:
    """Gana la escritura del turno más reciente, no la última en llegar (y una llamada terminada
    no vuelve a estar en curso). Así se comporta también el upsert de PostgreSQL."""
    store = MemoryStateStore(serialize=serialize)
    fresh: Any = {"call_id": "llam-1", "turn": 2, "finished": False}
    assert await store.save(fresh, 1.0) is True
    assert await store.save({**fresh, "turn": 1, "oldest": True}, 2.0) is False
    assert await store.save({**fresh, "finished": True}, 3.0) is True
    assert await store.save({**fresh, "last_delivery": {}}, 4.0) is False
    saved = await store.get("llam-1")
    assert saved is not None
    assert (saved["turn"], saved["finished"]) == (2, True)


class SlowStore(MemoryStateStore):
    """Como PostgreSQL: leer cede el control y otro turno puede escribir mientras tanto."""

    def __init__(self) -> None:
        super().__init__(serialize=True)
        self.was_read = asyncio.Event()
        self.proceed = asyncio.Event()
        self.should_pause = False

    async def get(self, call_id: str) -> Any:
        state = await super().get(call_id)
        if self.should_pause:
            self.should_pause = False
            self.was_read.set()
            await self.proceed.wait()
        return state


async def test_a_late_delivery_does_not_undo_the_next_turn() -> None:
    """La entrega del turno N lee el estado, y antes de escribirlo el turno N+1 ya se guardó:
    la entrega no puede devolver la llamada al turno N."""
    store = SlowStore()
    manager = CallManager(Dependencies(repo=_repo(), llm=FakeLLM()), store=store)
    state = await manager.begin()
    call_id = state["call_id"]
    store.should_pause = True
    delivery = asyncio.create_task(manager.record_delivery(call_id, 0, "interrupted", 0.5, "Hola"))
    await store.was_read.wait()
    upcoming = await manager.message(call_id, TURNS[0])
    store.proceed.set()
    returned = await delivery
    assert returned["turn"] == upcoming["turn"] == 1
    assert (await manager.get(call_id))["turn"] == 1
    assert manager.get_local(call_id)["turn"] == 1


# ------------------------------------------------------------------------------- PostgreSQL


def test_state_sql_model() -> None:
    table = CallStateSQL.__table__
    assert CallStateSQL.__tablename__ == "call_states"
    assert {c.name for c in table.columns} - set(STAMPS) == {
        "call_id",
        "state",
        "turn",
        "version",
        "finished",
        "updated_at",
    }
    assert isinstance(table.c.state.type.dialect_impl(postgresql.dialect()), postgresql.JSONB)


def _sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_save_is_an_upsert_that_bumps_the_version() -> None:
    sql = _sql(save_statement({"call_id": "llam-1", "turn": 3}, 10.0))  # type: ignore[typeddict-item]
    assert "INSERT INTO call_states" in sql
    assert "ON CONFLICT (call_id) DO UPDATE" in sql
    assert "version = (call_states.version + " in sql
    # Un estado de un turno anterior (o que reabre una llamada terminada) no pisa al guardado
    assert "WHERE excluded.turn >= call_states.turn" in sql
    assert "RETURNING call_states.call_id" in sql


def test_purge_deletes_with_the_same_policy_and_returns_the_ids() -> None:
    sql = _sql(purge_statement(10_000.0, 900, 7200))
    assert sql.startswith("DELETE FROM call_states")
    assert "call_states.finished" in sql
    assert "RETURNING call_states.call_id" in sql


def test_save_sets_the_time_in_utc() -> None:
    statement = save_statement({"call_id": "llam-1", "turn": 0}, 0.0)  # type: ignore[typeddict-item]
    values = statement.compile(dialect=postgresql.dialect()).params
    assert values["updated_at"] == datetime.fromtimestamp(0.0, UTC)
    assert values["version"] == 1


@pytest.mark.skipif(
    not os.environ.get("BC_TEST_DATABASE_URL"),
    reason="integración: necesita BC_TEST_DATABASE_URL apuntando a una base de pruebas",
)
async def test_postgres_integration_save_read_and_purge() -> None:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(os.environ["BC_TEST_DATABASE_URL"])
    async with engine.begin() as con:
        await con.run_sync(CallStateSQL.__table__.create, checkfirst=True)
    store = PostgresStateStore(async_sessionmaker(engine, expire_on_commit=False))
    state: Any = {"call_id": "llam-test-integracion", "turn": 2, "finished": True}
    try:
        await store.save(state, 0.0)
        assert await store.save({**state, "turn": 3}, 1.0) is True
        assert await store.save({**state, "turn": 1}, 2.0) is False
        assert (await store.get("llam-test-integracion"))["turn"] == 3  # type: ignore[index]
        assert "llam-test-integracion" in await store.purge(10_000.0, 900, 7200)
        assert await store.get("llam-test-integracion") is None
    finally:
        await store.discard("llam-test-integracion")
        await engine.dispose()
