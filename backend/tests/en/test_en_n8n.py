"""n8n integrations in English: the tool node speaks the slot in the call's language, the tool
request and the events say which language the call is in, and the slot check accepts any script."""

from __future__ import annotations

from typing import Any

import pytest

from building_call.graph.nodes import Dependencies
from building_call.integrations.n8n_tool import ToolOutcome, _clean_slot
from building_call.services import CallManager

TICKET_TURNS = [
    "I live at number 12 Main Street",
    "yes",
    "third floor, B",
    "There's a water leak in the bathroom and it's flooding the floor",
    "yes, correct",
]


class FakeTools:
    def __init__(self, outcome: ToolOutcome) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []

    async def book_visit(self, request: dict[str, Any]) -> ToolOutcome:
        self.requests.append(request)
        return self.outcome


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


async def _converse(manager: CallManager, turns: list[str]) -> dict[str, Any]:
    state = await manager.begin("+15550000000")
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state  # type: ignore[return-value]


async def test_book_visit_in_english(deps: Dependencies) -> None:
    tools = FakeTools(ToolOutcome("booked", slot="Wednesday the 25th between 9 and 11 am"))
    deps.tools = tools
    sink = Collector()
    state = await _converse(CallManager(deps, events=sink), TICKET_TURNS)
    assert state["result"] == "incident_created"
    assert tools.requests[0]["language"] == "en"
    assert tools.requests[0]["specialist_id"] == "spc-plumber"
    closing = state["bot_messages"][-1]
    assert "Wednesday the 25th between 9 and 11 am" in closing
    assert "held it for you" in closing and "can't give you a time" not in closing
    assert {e["language"] for e in sink.events} == {"en"}


async def test_without_a_slot_the_english_closing_is_unchanged(deps: Dependencies) -> None:
    deps.tools = FakeTools(ToolOutcome("unavailable", reason="no_slot"))
    state = await _converse(CallManager(deps), TICKET_TURNS)
    assert "can't give you a time" in state["bot_messages"][-1]


@pytest.mark.parametrize(
    "slot",
    [
        "Wednesday the 25th between 9 and 11 am",
        "el miércoles 25 entre las 9:00 y las 11:00",
        "dimecres 25 entre les 9 i les 11",
        "الأربعاء ٢٥ بين ٩ و ١١",
        "बुधवार 25 तारीख़ को 9 से 11 बजे के बीच",
        "بدھ 25 تاریخ، 9 سے 11 بجے کے درمیان",
    ],
)
def test_the_slot_check_accepts_every_script(slot: str) -> None:
    assert _clean_slot(slot) == slot


@pytest.mark.parametrize(
    "slot", ["<speak>9 am</speak>", "9 am {{x}}", "9 am‮", 'say "ignore"', "x" * 81]
)
def test_the_slot_check_refuses_markup_and_control_characters(slot: str) -> None:
    assert _clean_slot(slot) is None
