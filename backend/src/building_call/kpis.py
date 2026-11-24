"""Conversation KPIs, per call and aggregated, computed from the stored calls.

Inputs are what the operational repository already keeps: the call (result, start, end), its
turns (`list_turns`: nodes before/after, interpretation, server timings) and the graph decisions
(`list_decisions`). The client latency (`response_ms`) comes from the in-memory metrics
registry when it is there.

Definitions (one place, so the console and the README agree):

- contained: the assistant resolved the call alone (result `incident_created`).
- handed off: the caller asked for a person (result `handed_to_administrator`).
- needs a human: every finished call that was not contained (handed off, estate/home not
  identified, unclassifiable) except `abandoned`, which is counted apart.
- reprompt turn: a caller turn after which the conversation did not move on: the assistant
  stayed in the same node (asked again, repeated, answered a question, did not hear) or the
  caller asked for a repeat. The rate is reprompt turns / caller turns.
- emergency call: the graph reached `handle_emergency` and did not rule it out.
- turns: caller turns (the opening greeting has no caller utterance and does not count).
- handle time: `finished_at - started_at` of finished calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from building_call.domain.entities import Call

CONTAINED_RESULTS = frozenset({"incident_created"})
HANDOFF_RESULTS = frozenset({"handed_to_administrator"})
REPROMPT_INTENTS = frozenset({"repeat", "filler"})
EMERGENCY_NODE = "handle_emergency"


def _is_caller_turn(turn: dict[str, Any]) -> bool:
    return bool((turn.get("caller") or "").strip())


def _is_reprompt(turn: dict[str, Any]) -> bool:
    interpretation = turn.get("interpretation") or {}
    if interpretation.get("intent") in REPROMPT_INTENTS:
        return True
    before, after = turn.get("initial_node"), turn.get("final_node")
    return before is not None and before == after


def _is_emergency(turns: Sequence[dict[str, Any]], decisions: Sequence[dict[str, Any]]) -> bool:
    reached = any(d.get("node") == EMERGENCY_NODE for d in decisions) or any(
        EMERGENCY_NODE in (t.get("initial_node"), t.get("final_node")) for t in turns
    )
    ruled_out = any(d.get("action") == "emergency_ruled_out" for d in decisions)
    return reached and not ruled_out


def call_kpis(
    call: Call,
    turns: Sequence[dict[str, Any]],
    decisions: Sequence[dict[str, Any]] = (),
    response_ms: Sequence[float] = (),
) -> dict[str, Any]:
    caller_turns = [t for t in turns if _is_caller_turn(t)]
    reprompts = sum(1 for t in caller_turns if _is_reprompt(t))
    handle_s = (
        (call.finished_at - call.started_at).total_seconds()
        if call.finished_at is not None
        else None
    )
    server_ms = [
        float(t["timings"]["server_ms"])
        for t in caller_turns
        if isinstance((t.get("timings") or {}).get("server_ms"), int | float)
    ]
    llm_calls = sum(int((t.get("timings") or {}).get("llm_calls") or 0) for t in caller_turns)
    finished = call.state == "finished"
    return {
        "call_id": call.id,
        "result": call.result,
        "finished": finished,
        "contained": finished and call.result in CONTAINED_RESULTS,
        "handed_off": call.result in HANDOFF_RESULTS,
        "abandoned": call.result == "abandoned",
        "emergency": _is_emergency(turns, decisions),
        "turns": len(caller_turns),
        "reprompt_turns": reprompts,
        "reprompt_rate": reprompts / len(caller_turns) if caller_turns else 0.0,
        "llm_calls": llm_calls,
        "handle_time_s": round(handle_s, 1) if handle_s is not None else None,
        "response_ms": list(response_ms),
        "server_ms": server_ms,
    }


def percentile(values: Sequence[float], q: float) -> float:
    order = sorted(values)
    return float(order[max(0, min(len(order) - 1, round(q * (len(order) - 1))))])


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _latency(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.5) if values else None,
        "p95": percentile(values, 0.95) if values else None,
    }


def aggregate(per_call: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Rates over finished calls; turns and latency over every call."""
    finished = [c for c in per_call if c["finished"]]
    n = len(finished)
    turns = sum(c["turns"] for c in per_call)
    handle = [c["handle_time_s"] for c in finished if c["handle_time_s"] is not None]
    return {
        "calls": len(per_call),
        "finished_calls": n,
        "containment_rate": _rate(sum(c["contained"] for c in finished), n),
        "handoff_rate": _rate(sum(c["handed_off"] for c in finished), n),
        "needs_human_rate": _rate(
            sum(not c["contained"] and not c["abandoned"] for c in finished), n
        ),
        "abandon_rate": _rate(sum(c["abandoned"] for c in finished), n),
        "emergency_rate": _rate(sum(c["emergency"] for c in per_call), len(per_call)),
        "reprompt_rate": _rate(sum(c["reprompt_turns"] for c in per_call), turns),
        "turns_per_call": round(turns / len(per_call), 2) if per_call else None,
        "llm_calls_per_call": (
            round(sum(c["llm_calls"] for c in per_call) / len(per_call), 2) if per_call else None
        ),
        "avg_handle_time_s": round(sum(handle) / len(handle), 1) if handle else None,
        "response_ms": _latency([v for c in per_call for v in c["response_ms"]]),
        "server_ms": _latency([v for c in per_call for v in c["server_ms"]]),
    }
