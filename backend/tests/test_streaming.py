"""WebSocket de voz en streaming con una sesión STT falsa, y carrera de modelos."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from session_helpers import make_session

from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.llm.ollama import LLMError
from building_call.llm.race import RaceModel
from building_call.stt_streaming import (
    AssemblyAIConfigError,
    AssemblyAIStreamingSession,
    TurnEvent,
    validate_eu_endpoint,
)


class FakeSession:
    """Emite un 'final' cada vez que recibe la orden fin_turno; el texto sale de una cola."""

    texts = ["Calle Mayor 12", "sí"]

    def __init__(
        self, sample_rate: int, keyterms: list[str] | None = None, language: str | None = None
    ) -> None:
        self.sample_rate = sample_rate
        self.keyterms = keyterms or []
        self.audio = bytearray()
        self.connection_ms = 7
        self._queue: asyncio.Queue[TurnEvent | None] = asyncio.Queue()
        self.closed = False
        self._i = 0

    async def open(self) -> None:
        return None

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def force_end_of_turn(self) -> None:
        await self._queue.put(TurnEvent("partial", "calle ma"))
        await self._queue.put(TurnEvent("final", self.texts[self._i % len(self.texts)]))
        self._i += 1

    async def events(self):  # type: ignore[no-untyped-def]
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(None)


@pytest.fixture
def ws_client(deps: Dependencies) -> TestClient:
    c = TestClient(create_app(deps, streaming_factory=FakeSession))
    c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
    return c


def test_streaming_turn(ws_client: TestClient) -> None:
    call_id = ws_client.post("/api/calls", json={}).json()["call_id"]
    with ws_client.websocket_connect(f"/api/calls/{call_id}/voice?sample_rate=16000") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(b"\x00" * 3200)
        ws.send_json({"type": "end_turn"})
        assert ws.receive_json() == {"type": "partial", "text": "calle ma"}
        final = ws.receive_json()
        assert final["type"] == "final" and final["text"] == "Calle Mayor 12"
        assert "eou_ms" in final
        reply = ws.receive_json()
        assert reply["type"] == "reply"
        assert reply["current_node"] == "identify_estate"
        assert "Calle Mayor 12" in reply["bot_messages"][0]
        assert "stt_eou_to_response_ms" in reply["timings"]
        ws.send_json({"type": "pause"})
        ws.send_json({"type": "end_turn"})
        ws.receive_json()  # parcial
        ws.receive_json()  # final "sí"
        reply = ws.receive_json()
        assert reply["data"]["estate_id"] == "finca-mayor-12"


def test_the_voice_websocket_accepts_the_agent_secret(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Como el resto de rutas de llamada: el banco de voz compara este camino con LiveKit."""
    from building_call.settings import settings

    secret = "secreto-del-agente-con-largo-suficiente-0123456789"
    monkeypatch.setattr(settings, "agent_secret", secret)
    client = TestClient(create_app(deps, streaming_factory=FakeSession))
    headers = {"Authorization": f"Bearer {secret}"}
    call_id = client.post("/api/calls", json={}, headers=headers).json()["call_id"]
    path = f"/api/calls/{call_id}/voice?sample_rate=16000"
    with client.websocket_connect(path, headers=headers) as ws:
        assert ws.receive_json()["type"] == "ready"
    with client.websocket_connect(path, headers={"Authorization": "Bearer otro"}) as ws:
        assert ws.receive_json()["type"] == "error"


def test_unconfigured_streaming_returns_error(deps: Dependencies) -> None:
    client = TestClient(create_app(deps, streaming_factory=None))
    client.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
    call_id = client.post("/api/calls", json={}).json()["call_id"]
    with client.websocket_connect(f"/api/calls/{call_id}/voice") as ws:
        assert ws.receive_json()["type"] == "error"
    assert client.get("/api/health").json()["stt_streaming"] is None


def test_only_european_endpoints() -> None:
    assert validate_eu_endpoint("https://api.eu.assemblyai.com/") == "https://api.eu.assemblyai.com"
    with pytest.raises(AssemblyAIConfigError):
        validate_eu_endpoint("https://api.assemblyai.com")
    with pytest.raises(AssemblyAIConfigError):
        AssemblyAIStreamingSession("key", host="streaming.assemblyai.com")
    with pytest.raises(AssemblyAIConfigError):
        AssemblyAIStreamingSession("")


class SlowModel:
    def __init__(self, name: str, delay: float, result: dict[str, Any] | None) -> None:
        self.name = name
        self.delay = delay
        self.result = result

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:  # noqa: E501
        await asyncio.sleep(self.delay)
        if self.result is None:
            raise LLMError("falló")
        return self.result


async def test_race_returns_the_first_valid() -> None:
    race = RaceModel(
        SlowModel("slow", 0.2, {"a": 1}),
        SlowModel("fast", 0.01, {"a": 2}),
        SlowModel("roto", 0.0, None),
    )
    assert await race.extract("s", "u", {}) == {"a": 2}
    assert race.winners["fast"] == 1


async def test_race_fails_if_all_fail() -> None:
    race = RaceModel(SlowModel("a", 0.0, None), SlowModel("b", 0.0, None))
    with pytest.raises(LLMError):
        await race.extract("s", "u", {})


async def test_race_waits_for_the_preferred_if_it_arrives_in_time() -> None:
    preferred = SlowModel("exacto", 0.05, {"a": "exacto"})
    fast = SlowModel("fast", 0.0, {"a": "fast"})
    with_margin = RaceModel(preferred, fast, preferred=0, extra_wait_ms=300)
    assert (await with_margin.extract("s", "u", {}))["a"] == "exacto"
    no_margin = RaceModel(preferred, fast, preferred=0, extra_wait_ms=10)
    assert (await no_margin.extract("s", "u", {}))["a"] == "fast"


async def test_stt_vocabulary_includes_addresses_floors_and_doors(deps: Dependencies) -> None:
    from building_call.api.main import stt_vocabulary

    terms = await stt_vocabulary(deps.repo)
    assert "Calle Mayor 12" in terms and "Avenida de la Alameda 5" in terms
    assert "puerta A" in terms and "tercero B" in terms and "portal 2" in terms
    assert len(terms) <= 100


async def test_speculative_interpretation_is_reused(deps: Dependencies, llm: Any) -> None:
    import asyncio

    from building_call.graph.nodes import speculation_key
    from building_call.llm.interpretation import Interpretation
    from building_call.services import CallManager

    manager = CallManager(deps)
    state = await manager.begin()
    for t in ["Calle Mayor 12", "sí"]:
        state = await manager.message(state["call_id"], t)
    # El parcial "vivo en el tercero b y me llamo marta" ya se interpretó antes del final
    fut: asyncio.Future[Interpretation] = asyncio.get_running_loop().create_future()
    fut.set_result(Interpretation(floor="3", door="B", name="Marta", origin="llm"))
    deps.speculations[state["call_id"]] = {
        speculation_key("Vivo en el tercero B y me llamo Marta."): fut
    }
    n = len(llm.calls)
    state = await manager.message(state["call_id"], "Vivo en el tercero B y me llamo Marta.")
    assert len(llm.calls) == n  # no se volvió a llamar al modelo
    assert state["home_id"] == "finca-mayor-12-u-3B"
    assert "Marta" not in str({k: v for k, v in state.items() if k != "history"})
    assert state["turn_timings"]["interpretation_origin"] == "llm_speculative"
    assert deps.speculations == {}


async def test_speculation_from_another_call_is_neither_used_nor_cancelled(
    deps: Dependencies, llm: Any
) -> None:
    """Dos llamadas a la vez: el parcial de una no puede acabar en el expediente de la otra."""
    import asyncio

    from building_call.graph.nodes import speculation_key
    from building_call.llm.interpretation import Interpretation
    from building_call.services import CallManager

    manager = CallManager(deps)
    a = await manager.begin()
    b = await manager.begin()
    for t in ["Calle Mayor 12", "sí"]:
        a = await manager.message(a["call_id"], t)
        b = await manager.message(b["call_id"], t)
    phrase = "Vivo en el tercero B y me llamo Marta."
    foreign: asyncio.Future[Interpretation] = asyncio.get_running_loop().create_future()
    deps.speculations[a["call_id"]] = {speculation_key(phrase): foreign}
    b = await manager.message(b["call_id"], phrase)
    assert b["turn_timings"]["interpretation_origin"] != "llm_speculative"
    assert not foreign.cancelled()
    assert a["call_id"] in deps.speculations


async def test_ended_or_abandoned_calls_leave_memory(deps: Dependencies) -> None:
    """El expediente ya está en el repositorio: el estado vivo no debe crecer sin límite."""
    from building_call.services import CallManager, CallNotFound

    now = [0.0]
    manager = CallManager(deps, clock=lambda: now[0])
    done = await manager.begin()
    done["finished"] = True
    alive = await manager.begin()
    now[0] = CallManager.FINISHED_RETENTION_S + 1
    await manager.begin()  # cualquier turno barre lo caducado
    with pytest.raises(CallNotFound):
        await manager.get(done["call_id"])
    assert await manager.get(alive["call_id"])  # sigue activa: se conserva
    now[0] += CallManager.ABANDONED_RETENTION_S + 1
    await manager.begin()
    with pytest.raises(CallNotFound):
        await manager.get(alive["call_id"])


def test_metrics_do_not_grow_without_limit() -> None:
    from building_call.metrics import MetricsRegistry, TurnTimings

    registry = MetricsRegistry(capacity=3)
    for i in range(10):
        registry.record(
            TurnTimings(
                call_id="l", turn=i, initial_node="a", final_node="b", graph_ms=1, server_ms=1
            )
        )
    assert len(registry._index) == 3
    assert registry.complete_client("l", 0, {}) is None
    assert registry.complete_client("l", 9, {}) is not None
