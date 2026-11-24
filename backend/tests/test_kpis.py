"""Conversation KPIs: per call, aggregated, and through the console API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from session_helpers import TEST_SECRET, make_session
from httpx import ASGITransport, AsyncClient

from building_call.api.main import create_app
from building_call.domain.entities import Call
from building_call.graph.nodes import Dependencies
from building_call.kpis import aggregate, call_kpis
from building_call.settings import settings as global_settings

T0 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


def _call(cid: str, result: str | None, seconds: float | None = 60) -> Call:
    return Call(
        id=cid,
        started_at=T0,
        result=result,  # type: ignore[arg-type]
        state="finished" if seconds is not None else "active",
        finished_at=T0 + timedelta(seconds=seconds) if seconds is not None else None,
    )


def _turn(before: str | None, after: str, caller: str = "x", **extra: Any) -> dict[str, Any]:
    return {"caller": caller, "initial_node": before, "final_node": after, **extra}


def test_call_kpis() -> None:
    turns = [
        _turn(None, "identify_estate", caller=""),  # greeting: not a caller turn
        _turn("identify_estate", "identify_home", timings={"server_ms": 300, "llm_calls": 1}),
        _turn("identify_home", "identify_home", interpretation={"intent": "question"}),
        _turn("identify_home", "collect_request", interpretation={"intent": "repeat"}),
        _turn("collect_request", "end", timings={"server_ms": 900, "llm_calls": 1}),
    ]
    k = call_kpis(_call("a", "incident_created", 95), turns, response_ms=[650, 1100])
    assert k["turns"] == 4 and k["reprompt_turns"] == 2 and k["reprompt_rate"] == 0.5
    assert k["contained"] and not k["handed_off"] and not k["emergency"]
    assert k["handle_time_s"] == 95 and k["llm_calls"] == 2
    assert k["server_ms"] == [300.0, 900.0] and k["response_ms"] == [650, 1100]


def test_emergency_unless_ruled_out() -> None:
    reached = [{"node": "handle_emergency", "action": "safety_instruction"}]
    ruled_out = [*reached, {"node": "handle_emergency", "action": "emergency_ruled_out"}]
    assert call_kpis(_call("a", "incident_created"), [], reached)["emergency"]
    assert not call_kpis(_call("a", "incident_created"), [], ruled_out)["emergency"]


def test_aggregate_rates() -> None:
    per_call = [
        call_kpis(_call("a", "incident_created", 60), [_turn("n", "m")], response_ms=[500]),
        call_kpis(_call("b", "handed_to_administrator", 30), [_turn("n", "n")]),
        call_kpis(_call("c", "abandoned", 10), []),
        call_kpis(_call("d", "home_not_identified", 120), [_turn("n", "m")]),
        call_kpis(_call("e", None, None), [_turn("n", "m"), _turn("m", "m")]),  # in progress
    ]
    a = aggregate(per_call)
    assert a["calls"] == 5 and a["finished_calls"] == 4
    assert a["containment_rate"] == 0.25 and a["handoff_rate"] == 0.25
    assert a["abandon_rate"] == 0.25 and a["needs_human_rate"] == 0.5
    assert a["reprompt_rate"] == pytest.approx(round(2 / 5, 4))
    assert a["turns_per_call"] == 1.0
    assert a["avg_handle_time_s"] == 55.0
    assert a["response_ms"] == {"n": 1, "p50": 500.0, "p95": 500.0}


def test_aggregate_of_nothing() -> None:
    a = aggregate([])
    assert a["calls"] == 0 and a["containment_rate"] is None and a["turns_per_call"] is None


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_kpis_api(client: AsyncClient) -> None:
    assert (await client.get("/api/operations/kpis")).status_code == 401
    repo = client._transport.app.state.repo  # type: ignore[attr-defined]
    client.cookies.set("bc_session", make_session(repo))
    cid = (await client.post("/api/calls", json={"caller_phone": "+34600"})).json()["call_id"]
    for text in ["Calle Mayor 12", "sí", "¿Cómo?", "3 B", "Gotea el grifo", "sí"]:
        await client.post(f"/api/calls/{cid}/messages", json={"text": text})

    body = (await client.get("/api/operations/kpis")).json()
    a = body["aggregate"]
    assert a["calls"] == 1 and a["containment_rate"] == 1.0 and a["handoff_rate"] == 0.0
    assert a["turns_per_call"] == 6.0 and a["reprompt_rate"] > 0
    one = (await client.get(f"/api/operations/calls/{cid}/kpis")).json()
    assert one["contained"] and one["reprompt_turns"] >= 1
    assert "transcript" not in one and "caller_phone" not in one
    assert (await client.get("/api/operations/calls/nope/kpis")).status_code == 404
