"""A short scripted call in each of the six languages, through the real graph and the call
manager, with the model mocked: the deterministic paths (yes/no, apartments) run for real in
each language, and what the caller hears comes from that language's templates."""

from __future__ import annotations

import re
from typing import Any

import pytest

from building_call import i18n
from building_call.graph.nodes import Dependencies
from building_call.graph.templates import render
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager

_ANSWER = re.compile(r'(?:Respuesta del vecino|Caller\'s answer): """(.*)"""', re.DOTALL)


class ScriptedLLM:
    """Answers the interpretation of each known sentence as a model would; the rest, nothing."""

    name = "scripted"

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers = answers
        self.systems: list[str] = []

    async def extract(
        self, system: str, user: str, schema: dict[str, Any], format_instruction: str | None = None
    ) -> dict[str, Any]:
        self.systems.append(system)
        m = _ANSWER.search(user)
        return dict(self.answers.get(m.group(1).strip() if m else "", {}))


LEAK = {"category": "plumbing", "urgency": "high", "work_type": "repair"}

#: language -> (demo set, turns, scripted model answers, words the summary read-back contains)
SCRIPTS: dict[str, tuple[str, list[str], dict[str, dict[str, Any]], str]] = {
    "es": (
        "es",
        ["Vivo en la calle Mayor 12", "sí", "tercero B", "sí", "Hay una fuga de agua en el baño", "sí"],
        {
            "Vivo en la calle Mayor 12": {"direccion": "Calle Mayor 12"},
            "Hay una fuga de agua en el baño": {
                "problema": "fuga de agua en el baño",
                "categoria": "fontaneria",
                "urgencia": "alta",
                "resumen": "fuga de agua en el baño",
            },
        },
        "fuga de agua",
    ),
    "en": (
        "en",
        ["I live at 12 Main Street", "yes", "apartment 3B", "yes", "There's a water leak in the bathroom", "yes"],
        {
            "I live at 12 Main Street": {"address": "12 Main Street"},
            "There's a water leak in the bathroom": {
                "problem": "water leak in the bathroom",
                "summary": "water leak in the bathroom",
                **LEAK,
            },
        },
        "water leak",
    ),
    "ca": (
        "ca",
        ["Visc al carrer Major 12", "sí", "tercer B", "sí", "Hi ha una fuita d'aigua al bany", "sí"],
        {
            "Visc al carrer Major 12": {"address": "Calle Mayor 12"},
            "Hi ha una fuita d'aigua al bany": {
                "problem": "fuita d'aigua al bany",
                "summary": "fuita d'aigua al bany",
                **LEAK,
            },
        },
        "fuita d'aigua",
    ),
    "ar": (
        "en",
        ["أسكن في 12 Main Street", "نعم", "شقة 3B", "نعم", "يوجد تسرب ماء في الحمام", "نعم"],
        {
            "أسكن في 12 Main Street": {"address": "12 Main Street"},
            "يوجد تسرب ماء في الحمام": {
                "problem": "تسرب ماء في الحمام",
                "summary": "تسرب ماء في الحمام",
                **LEAK,
            },
        },
        "تسرب ماء",
    ),
    "hi": (
        "en",
        ["मैं 12 Main Street में रहता हूँ", "हाँ", "फ्लैट 3B", "हाँ", "बाथरूम में पानी का रिसाव है", "हाँ"],
        {
            "मैं 12 Main Street में रहता हूँ": {"address": "12 Main Street"},
            "बाथरूम में पानी का रिसाव है": {
                "problem": "बाथरूम में पानी का रिसाव",
                "summary": "बाथरूम में पानी का रिसाव",
                **LEAK,
            },
        },
        "पानी का रिसाव",
    ),
    "ur": (
        "en",
        ["میں 12 Main Street میں رہتا ہوں", "جی ہاں", "فلیٹ 3B", "جی ہاں", "باتھ روم میں پانی لیک ہو رہا ہے", "جی ہاں"],
        {
            "میں 12 Main Street میں رہتا ہوں": {"address": "12 Main Street"},
            "باتھ روم میں پانی لیک ہو رہا ہے": {
                "problem": "باتھ روم میں پانی کا رساؤ",
                "summary": "باتھ روم میں پانی کا رساؤ",
                **LEAK,
            },
        },
        "پانی کا رساؤ",
    ),
}  # fmt: skip


def _repo(demo: str) -> MemoryRepository:
    repo = MemoryRepository(demo)
    repo.administrator_config["adm-demo"] = repo.administrator_config["adm-demo"].model_copy(
        update={"ask_detail": False}
    )
    return repo


@pytest.mark.parametrize("code", list(SCRIPTS))
async def test_a_short_call_in_each_language_creates_the_incident(code: str) -> None:
    demo, turns, answers, summary_words = SCRIPTS[code]
    repo = _repo(demo)
    manager = CallManager(Dependencies(repo=repo, llm=ScriptedLLM(answers)))

    state = await manager.begin(language=code)
    with i18n.use(code):
        assert state["bot_messages"][0] == render("identify_estate.greeting")
    for text in turns:
        state = await manager.message(state["call_id"], text)

    assert state["language"] == code and state["language_locked"] is True
    assert state["result"] == "incident_created", state["trace"][-3:]
    [incident] = await repo.list_incidents()
    assert incident.category == "plumbing"
    bot = [h["text"] for h in state["history"] if h["role"] == "bot"]
    with i18n.use(code):
        # The apartment and the request were read back in the caller's language
        assert any(t.startswith(render("confirm_home.ask", address="", home="")[:4]) for t in bot)
        assert any(summary_words in t for t in bot)
        # The yes/no answers never needed the model: deterministic in every language
        assert "home_confirmed_verbally" in state["validations"]


@pytest.mark.parametrize("code", list(SCRIPTS))
def test_each_language_reads_its_own_yes_and_apartment(code: str) -> None:
    _, turns, _, _ = SCRIPTS[code]
    parser = i18n.parser_for(code)
    assert parser.yes_no(turns[1]) is True
    entrance, floor, door = parser.extract_home(turns[2])
    assert (floor, door) == ("3", "B")
