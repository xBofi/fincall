"""Vista de enrutado: estructura exportada del grafo compilado y traza de visitas por nodo."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import TEST_SECRET, make_session

from building_call import i18n
from building_call.api.main import create_app
from building_call.graph.export import export_graph
from building_call.graph.manifest import MANIFEST
from building_call.graph.nodes import Dependencies
from building_call.graph.registry import get_graph
from building_call.graph.tracing import TRACE_BUS, TraceBus, condition_for, state_diff
from building_call.services import CallManager
from building_call.settings import settings as global_settings

CONVERSATION = [
    "Vivo en la calle Mayor número 12",
    "sí",
    "tercero B",
    "sí",  # confirma la vivienda leída junto a la finca (confirm_home)
    "Hay una fuga de agua en el baño y está inundando el suelo",
    "sí, correcto",
]

# ------------------------------------------------------------------------- exportación del grafo


def test_the_export_has_exactly_the_compiled_nodes(deps: Dependencies) -> None:
    compiled = set(get_graph("standard", deps).get_graph().nodes)
    exported = {n["id"] for n in export_graph()["nodes"]}
    assert exported == compiled
    assert export_graph()["drift"] == []


def test_every_declared_route_is_a_compiled_conditional_edge(deps: Dependencies) -> None:
    drawable = get_graph("standard", deps).get_graph()
    conditional = {(e.source, e.target) for e in drawable.edges if e.conditional}
    g = export_graph()
    declared = {(m, a.target) for m, n in MANIFEST.items() for a in n.targets}
    assert len(g["edges"]) == len(declared)
    for edge in g["edges"]:
        assert (edge["source"], edge["target"]) in conditional
        assert edge["conditional"] is True
        assert edge["condition"], "una ruta sin motivo no explica nada en la vista"
        assert edge["router"] == "_next"


def test_node_roles_cover_the_legend() -> None:
    roles = {n["id"]: n["role"] for n in export_graph()["nodes"]}
    assert roles["__start__"] == "entry"
    assert roles["__end__"] == "end"
    assert roles["next_step"] == "router"
    assert roles["identify_estate"] == "llm"
    assert roles["create_ticket"] == "tool"
    assert roles["handoff_to_human"] == "human"
    assert roles["farewell"] == "end"
    assert set(roles.values()) == {"entry", "router", "llm", "tool", "human", "end"}


def test_edge_kinds_tell_the_layout_what_is_a_loop() -> None:
    kinds = {e["id"]: e["kind"] for e in export_graph()["edges"]}
    assert kinds["identify_estate->identify_estate"] == "retry"
    assert kinds["identify_estate->next_step"] == "return"
    assert kinds["identify_estate->handoff_to_human"] == "global"
    assert kinds["next_step->identify_estate"] == "forward"
    assert kinds["notify_channel->__end__"] == "forward"


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        c.cookies.set("bc_session", make_session(deps.repo, "owner"))  # type: ignore[arg-type]
        yield c


async def test_structure_endpoint_serves_the_export(client: AsyncClient) -> None:
    r = await client.get("/api/graph/structure")
    assert r.status_code == 200
    body = r.json()
    assert body["graph"] == "standard"
    assert body["entry"] == "__start__" and body["end"] == "__end__"
    assert body["drift"] == [] and body["violations"] == []
    assert all(e["times"] >= 0 for e in body["edges"])
    assert body["compiled"]["conditional_edges"] >= len(body["edges"])


async def test_structure_endpoint_rejects_unknown_graphs_and_anonymous(
    client: AsyncClient,
) -> None:
    assert (await client.get("/api/graph/structure?name=nope")).status_code == 404
    anonymous = AsyncClient(transport=client._transport, base_url="http://localhost")
    assert (await anonymous.get("/api/graph/structure")).status_code == 401


async def test_structure_counts_routes_taken_by_saved_calls(client: AsyncClient) -> None:
    call = (await client.post("/api/calls", json={})).json()
    for text in CONVERSATION:
        await client.post(f"/api/calls/{call['call_id']}/messages", json={"text": text})
    edges = {e["id"]: e for e in (await client.get("/api/graph/structure")).json()["edges"]}
    assert edges["next_step->identify_estate"]["times"] >= 1


# -------------------------------------------------------------------------------- visitas


def test_condition_comes_from_the_manifest() -> None:
    cond, declared = condition_for("next_step", "identify_estate", consumed_utterance=False)
    assert declared and cond == "no hay finca, o hay una candidata que confirmar"
    # Preguntar al llegar no es la auto-arista "repregunta"
    assert condition_for("identify_estate", "identify_estate", False) == (None, True)
    cond, declared = condition_for("identify_estate", "identify_estate", True)
    assert declared and cond and cond.startswith("repregunta")
    assert condition_for("identify_estate", "create_ticket", True) == (None, False)


def test_state_diff_hides_personal_and_noisy_keys() -> None:
    diff = state_diff(
        {"estate_id": None, "caller_phone": None, "history": []},
        {"estate_id": "finca-1", "caller_phone": "+34600000000", "history": [{"x": 1}]},
    )
    assert diff == {"estate_id": {"before": None, "after": "finca-1"}}


async def test_each_turn_records_its_node_visits(manager: CallManager) -> None:
    state = await manager.begin()
    visits = state["turn_visits"]
    assert [v["node"] for v in visits] == ["next_step", "identify_estate"]
    first, second = visits
    assert first["target"] == "identify_estate"
    assert first["condition"] == "no hay finca, o hay una candidata que confirmar"
    assert second["awaiting_user"] is True and second["condition"] is None
    assert all(v["ms"] >= 0 and v["declared"] for v in visits)
    assert [v["index"] for v in visits] == [0, 1]
    assert second["said"], "el saludo sale de la visita que pregunta"


async def test_visits_explain_llm_and_state_changes(manager: CallManager) -> None:
    state = await manager.begin()
    for text in CONVERSATION[:4]:
        state = await manager.message(state["call_id"], text)
    state = await manager.message(state["call_id"], CONVERSATION[4])
    by_node = {v["node"]: v for v in state["turn_visits"]}
    collect = by_node["collect_request"]
    assert collect["llm_calls"] == 1
    assert collect["interpretation"]["origin"] == "llm"
    assert "name" not in collect["interpretation"]
    assert collect["consumed_utterance"] is True
    # La solicitud se recoge en `next_step`, que consume lo pendiente
    changed = {k for v in state["turn_visits"] for k in v["diff"]}
    assert "request" in changed or "pending" in changed
    for v in state["turn_visits"]:
        assert "caller_phone" not in v["diff"]


async def test_visits_are_saved_with_the_turn(manager: CallManager) -> None:
    state = await manager.begin()
    state = await manager.message(state["call_id"], CONVERSATION[0])
    turns = await manager.deps.repo.list_turns(state["call_id"])  # type: ignore[attr-defined]
    assert turns[-1]["visits"] == state["turn_visits"]
    assert turns[-1]["visits"][0]["turn"] == state["turn"]


async def test_finished_call_ends_at_end(manager: CallManager) -> None:
    state = await manager.begin()
    for text in CONVERSATION:
        state = await manager.message(state["call_id"], text)
    assert state["finished"]
    last = state["turn_visits"][-1]
    assert last["node"] == "notify_channel" and last["target"] == "end"
    assert last["condition"] == "incidencia creada y vecino informado"


# ------------------------------------------------------------------------------------- en vivo


async def test_the_bus_streams_enter_exit_and_turn_end(manager: CallManager) -> None:
    state = await manager.begin()
    queue = TRACE_BUS.subscribe(state["call_id"])
    try:
        await manager.message(state["call_id"], CONVERSATION[0])
        events: list[dict[str, Any]] = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        TRACE_BUS.unsubscribe(state["call_id"], queue)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "node_enter" and kinds[-1] == "turn_end"
    assert kinds.count("node_enter") == kinds.count("node_exit") >= 1
    enter, leave = events[0], events[1]
    assert enter["node"] == leave["node"] and enter["index"] == leave["index"] == 0
    assert TRACE_BUS.subscribers(state["call_id"]) == 0


def test_a_slow_console_never_blocks_the_call() -> None:
    bus = TraceBus()
    queue = bus.subscribe("c")
    for i in range(bus.QUEUE_SIZE + 10):
        bus.publish("c", {"type": "node_enter", "i": i})
    assert queue.qsize() == bus.QUEUE_SIZE
    bus.publish("nadie-mira", {"type": "x"})  # sin suscriptores no pasa nada


async def test_sse_sends_a_snapshot_and_closes_for_a_finished_call(client: AsyncClient) -> None:
    call = (await client.post("/api/calls", json={})).json()
    for text in CONVERSATION:
        await client.post(f"/api/calls/{call['call_id']}/messages", json={"text": text})
    r = await asyncio.wait_for(client.get(f"/api/calls/{call['call_id']}/events"), 5)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    event, data = r.text.strip().split("\n", 1)
    assert event == "event: snapshot"
    snapshot = json.loads(data.removeprefix("data: "))
    assert snapshot["finished"] is True
    assert snapshot["visits"][-1]["node"] == "notify_channel"


async def test_sse_unknown_call_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/calls/llam-nope/events")).status_code == 404


# ------------------------------------------------------------------------------------- la demo

DEMO = Path(__file__).resolve().parents[2] / "frontend/src/components/routing/demo/demoCall.json"


def test_the_recorded_demo_still_matches_the_compiled_graph() -> None:
    """Si el grafo cambia, hay que volver a grabar la demo (evals/record_demo_call.py)."""
    demo = json.loads(DEMO.read_text(encoding="utf-8"))
    current = export_graph()
    assert [n["id"] for n in demo["structure"]["nodes"]] == [n["id"] for n in current["nodes"]]
    assert [(e["id"], e["condition"]) for e in demo["structure"]["edges"]] == [
        (e["id"], e["condition"]) for e in current["edges"]
    ]
    declared = {e["id"] for e in current["edges"]}
    for turn in demo["turns"]:
        for v in turn["visits"]:
            target = "__end__" if v["target"] == "end" else v["target"]
            assert v["declared"]
            assert v["node"] == target or f"{v['node']}->{target}" in declared


# ---------------------------------------------------------------------------- los seis idiomas

LANGUAGES = ["es", "en", "ca", "ar", "hi", "ur"]


def test_the_six_languages_are_the_supported_ones() -> None:
    assert sorted(LANGUAGES) == sorted(i18n.supported())


@pytest.mark.parametrize("code", LANGUAGES)
async def test_the_trace_is_the_same_graph_in_every_language(
    manager: CallManager, code: str
) -> None:
    """La ruta no depende del idioma: mismos nodos, mismas condiciones declaradas; lo que se
    le dice al vecino sale en su idioma y queda en la visita que lo dijo."""
    state = await manager.begin(language=code)
    visits = state["turn_visits"]
    assert [(v["node"], v["target"]) for v in visits] == [
        ("next_step", "identify_estate"),
        ("identify_estate", "identify_estate"),
    ]
    assert visits[0]["condition"] == "no hay finca, o hay una candidata que confirmar"
    assert all(v["declared"] for v in visits)
    assert visits[1]["said"] == state["bot_messages"]


async def test_each_language_says_its_own_greeting_in_the_trace(manager: CallManager) -> None:
    said = {}
    for code in LANGUAGES:
        state = await manager.begin(language=code)
        said[code] = state["turn_visits"][-1]["said"][0]
    assert len(set(said.values())) == len(LANGUAGES)


@pytest.mark.parametrize("code", LANGUAGES)
async def test_the_call_view_carries_the_visits_in_every_language(client: AsyncClient, code: str) -> None:
    call = (await client.post("/api/calls", json={"language": code})).json()
    assert call.get("language", code) == code
    assert [v["node"] for v in call["visits"]] == ["next_step", "identify_estate"]
    again = (await client.get(f"/api/calls/{call['call_id']}")).json()
    assert again["visits"] == call["visits"]
