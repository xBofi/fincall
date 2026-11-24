from __future__ import annotations

from typing import Any

from building_call.graph.nodes import Dependencies
from building_call.llm.phrasing import keeps_facts, phrase, required_facts
from building_call.services import CallManager


def test_required_facts_extracts_numbers_references_and_names() -> None:
    template = (
        "I have 5 Elm Avenue. Is that right? Perfect, apartment 3B. "
        "I've logged your issue with reference inc-abc123. Smith Plumbing will be notified."
    )
    facts = required_facts(template)
    assert "5" in facts and "3B" in facts and "inc-abc123" in facts
    assert "Elm Avenue" in facts and "Smith Plumbing" in facts
    # Sentence openers are not facts
    assert not facts & {"Perfect", "Is", "I've", "I"}


def test_keeps_facts_detects_omissions() -> None:
    template = "I have 12 Main Street. Is that right?"
    assert keeps_facts(template, "Got it, do you live at 12 Main Street?")
    assert not keeps_facts(template, "Got it, do you live on Main Street?")
    assert not keeps_facts(template, "You live at 12 Main Street.")  # lost the question


class FakePhraser:
    name = "phraser"

    def __init__(self, reply: str | None, tarda: float = 0) -> None:
        self.reply = reply
        self.tarda = tarda

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:  # noqa: E501
        import asyncio

        await asyncio.sleep(self.tarda)
        if self.reply is None:
            raise RuntimeError("down")
        return {"reply": self.reply}


async def test_phrase_uses_template_if_it_loses_facts_or_fails() -> None:
    template = "I have 12 Main Street. Is that right?"
    text, m = await phrase(FakePhraser("Do you live on Main Street?"), template, "on main", {})
    assert text == template and m["phrasing"].startswith("template")
    text, m = await phrase(FakePhraser(None), template, "on main", {})
    assert text == template and m["phrasing"].startswith("failure")
    text, m = await phrase(FakePhraser("x", tarda=0.3), template, "on main", {}, timeout_s=0.05)
    assert text == template
    text, m = await phrase(
        FakePhraser("Got it. Do you live at 12 Main Street?"), template, "on main", {}
    )
    assert text == "Got it. Do you live at 12 Main Street?" and m["phrasing"] == "llm"


async def test_manager_phrases_only_llm_interpreted_turns(deps: Dependencies) -> None:
    manager = CallManager(deps, phraser=deps.llm)  # the fake LLM prepends "Okay. "
    state = await manager.begin()
    assert state["bot_messages"][0].startswith("Hi")  # greeting: template as it is
    state = await manager.message(state["call_id"], "12 Main Street")
    assert state["bot_messages"] == ["I have 12 Main Street. Is that right?"]  # determinista
    state = await manager.message(state["call_id"], "yes")
    state = await manager.message(
        state["call_id"], "Well, look, I live in 3 B and my name is Martha, and there's a leak"
    )
    # Turno interpretado por el LLM: se redacta y el historial guarda lo dicho
    assert state["bot_messages"][0].startswith("Okay. ")
    assert state["history"][-1]["text"] == state["bot_messages"][0]
    assert state["history"][-2]["role"] == "caller"
    assert state["turn_timings"]["phrasing"] == "llm"


def test_no_local_phrasing_with_api_provider(monkeypatch: Any) -> None:
    # Con Vertex o Workers AI no hay Ollama: cada turno esperaba en balde 750 ms
    from building_call.api.main import create_phraser
    from building_call.settings import settings

    monkeypatch.setattr(settings, "phrasing", "llm")
    for provider in ("vertex", "workers_ai"):
        monkeypatch.setattr(settings, "llm", provider)
        assert create_phraser() is None
    monkeypatch.setattr(settings, "llm", "ollama")
    assert create_phraser() is not None
