"""The n8n integrations in each of the six languages, on the scripted calls of
`test_multilingual_calls`: the tool request and the events carry the call's language, the slot
n8n returns (in that language and script) is spoken, and the closing always says the visit is
provisional and pending the maintainer's confirmation."""

from __future__ import annotations

from typing import Any

import pytest
from test_multilingual_calls import SCRIPTS, ScriptedLLM, _repo
from test_n8n_integrations import PROVISIONAL_WORDS

from building_call import i18n
from building_call.graph.nodes import Dependencies
from building_call.integrations.n8n_tool import ToolOutcome, _clean_slot
from building_call.services import CallManager

#: What the booking workflow answers in each language (see `integrations/n8n/build_workflows.py`)
SLOTS = {
    "es": "el miércoles, 25 de noviembre, entre las 9:00 y las 11:00",
    "en": "on Wednesday, November 25, between 9 and 11 AM",
    "ca": "dimecres, 25 de novembre, entre les 9:00 i les 11:00",
    "ar": "يوم الأربعاء، ٢٥ نوفمبر، بين ٩ و١١ صباحا",
    "hi": "बुधवार, 25 नवंबर को सुबह 9 से 11 बजे के बीच",
    "ur": "بدھ، 25 نومبر کو صبح 9 سے 11 بجے کے درمیان",
}


class FakeTools:
    def __init__(self, slot: str) -> None:
        self.slot = slot
        self.requests: list[dict[str, Any]] = []

    async def book_visit(self, request: dict[str, Any]) -> ToolOutcome:
        self.requests.append(request)
        return ToolOutcome("booked", slot=self.slot, booking_id="b1")


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_every_language_has_a_slot_and_provisional_wording() -> None:
    assert set(SLOTS) == set(i18n.supported()) == set(PROVISIONAL_WORDS)


@pytest.mark.parametrize("code", i18n.supported())
def test_the_slot_of_each_language_passes_the_check(code: str) -> None:
    assert _clean_slot(SLOTS[code]) == SLOTS[code]


@pytest.mark.parametrize("code", i18n.supported())
async def test_book_visit_and_events_in_each_language(code: str) -> None:
    demo, turns, answers, _ = SCRIPTS[code]
    repo = _repo(demo)
    tools, sink = FakeTools(SLOTS[code]), Collector()
    manager = CallManager(
        Dependencies(repo=repo, llm=ScriptedLLM(answers), tools=tools), events=sink
    )
    state = await manager.begin(language=code)
    for text in turns:
        state = await manager.message(state["call_id"], text)

    assert state["result"] == "incident_created", state["trace"][-3:]
    assert tools.requests and tools.requests[0]["language"] == code
    closing = state["bot_messages"][-1]
    assert SLOTS[code] in closing
    for words in PROVISIONAL_WORDS[code]:
        assert words in closing.lower(), (code, words, closing)
    assert [e["type"] for e in sink.events] == ["call.started", "ticket.created", "call.ended"]
    assert {e["language"] for e in sink.events} == {code}
