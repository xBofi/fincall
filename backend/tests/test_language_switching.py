"""Picking the call's language: detection from the first words, a confidence threshold with a
safe fallback, switching mid-call when the caller asks, and the providers following it."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from es_helpers import FakeLLM

from building_call import i18n
from building_call.graph.nodes import Dependencies
from building_call.graph.templates import render
from building_call.i18n import detection, personas
from building_call.i18n.formatting import format_currency, format_date, format_number
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager
from building_call.settings import settings

# ------------------------------------------------------------------------------- detection


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Hola, vivo en la calle Mayor y no hay luz en la escalera", "es"),
        ("Hi, I live at 12 Main Street and the elevator is not working", "en"),
        ("Bon dia, visc al carrer Major i no funciona l'ascensor", "ca"),
        ("مرحبا، المصعد لا يعمل في المبنى", "ar"),
        ("नमस्ते, लिफ्ट काम नहीं कर रही है", "hi"),
        ("السلام علیکم، لفٹ کام نہیں کر رہی ہے", "ur"),
    ],
)
def test_detects_the_language_of_a_sentence(text: str, language: str) -> None:
    assert detection.detect(text).language == language


def test_short_or_numeric_sentences_say_nothing() -> None:
    assert detection.detect("12").language is None
    assert detection.detect("Sí").language is None  # one word: not enough evidence
    assert detection.detect("3B").language is None


def test_the_stt_report_wins_when_it_is_sure() -> None:
    found = detection.decide("ok", stt_language="en", stt_confidence=0.95)
    assert found.language == "en" and found.source == "stt"
    # Unsure and contradicted by the text: the text wins
    found = detection.decide(
        "Hola, no funciona el ascensor del portal", stt_language="en", stt_confidence=0.4
    )
    assert found.language == "es"


@pytest.mark.parametrize(
    ("text", "current", "asked"),
    [
        ("English, please", "es", "en"),
        ("¿Puede hablar en inglés?", "es", "en"),
        ("Can you speak Spanish?", "en", "es"),
        ("عربي من فضلك", "en", "ar"),
        ("No hablo inglés", "es", None),  # asks for nothing
        ("Calle Inglés 3", "es", None),  # an address, not a request
        ("Spanish", "es", None),  # already speaking it
    ],
)
def test_asking_for_a_language_by_name(text: str, current: str, asked: str | None) -> None:
    assert detection.requested_language(text, current) == asked


# ------------------------------------------------------------------------------- switching


@pytest.fixture
def manager() -> CallManager:
    return CallManager(Dependencies(repo=MemoryRepository("es"), llm=FakeLLM()))


async def test_first_words_in_another_language_switch_the_call(manager: CallManager) -> None:
    state = await manager.begin()  # nobody chose the language: BC_LANGUAGE (es), not locked
    assert state["language"] == "es" and state["language_locked"] is False
    state = await manager.message(
        state["call_id"], "Hi, I'm calling because the elevator in my building is not working"
    )
    assert state["language"] == "en" and state["language_locked"] is True
    assert any(t["node"] == "language" and "es -> en" in t["detail"] for t in state["trace"])
    with i18n.use("en"):
        assert any(m.startswith(("Sorry", "I ", "Which", "Hi")) for m in state["bot_messages"])


async def test_low_confidence_keeps_the_default_language(manager: CallManager) -> None:
    state = await manager.begin()
    state = await manager.message(state["call_id"], "Calle Mayor 12")
    assert state["language"] == "es"


async def test_a_chosen_language_is_never_switched_by_detection(manager: CallManager) -> None:
    state = await manager.begin(language="es")
    state = await manager.message(
        state["call_id"], "Hi, I'm calling because the elevator in my building is not working"
    )
    assert state["language"] == "es"


async def test_detection_only_looks_at_the_first_turns(
    manager: CallManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "language_detection_turns", 1)
    state = await manager.begin()
    state = await manager.message(state["call_id"], "Calle Mayor 12")
    state = await manager.message(
        state["call_id"], "Hi, the elevator in the building is not working"
    )
    assert state["language"] == "es"


async def test_detection_can_be_turned_off(
    manager: CallManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "language_detection", False)
    state = await manager.begin()
    state = await manager.message(
        state["call_id"], "Hi, the elevator in my building is not working"
    )
    assert state["language"] == "es"


async def test_asking_for_english_mid_call_asks_the_question_again_in_english(
    manager: CallManager,
) -> None:
    state = await manager.begin(language="es")
    state = await manager.message(state["call_id"], "Calle Mayor 12")
    state = await manager.message(state["call_id"], "sí")
    assert state["current_node"] == "identify_home"
    turn = state["turn"]
    state = await manager.message(state["call_id"], "Sorry, English please")
    assert state["language"] == "en" and state["turn"] == turn + 1
    assert state["current_node"] == "identify_home"
    with i18n.use("en"):
        assert state["bot_messages"] == [render("identify_home.ask")]
    assert state["history"][-2] == {"role": "caller", "text": "Sorry, English please"}
    # And the call goes on in English
    state = await manager.message(state["call_id"], "apartment 3B")
    assert state["home_id"] == "finca-mayor-12-u-3B"
    assert "apartment 3B" in state["bot_messages"][-1]


async def test_only_enabled_languages_are_detected(
    manager: CallManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "languages", ["es", "ca"])
    assert i18n.enabled() == ("es", "ca")
    state = await manager.begin()
    state = await manager.message(
        state["call_id"], "Hi, the elevator in my building is not working"
    )
    assert state["language"] == "es"


# ------------------------------------------------------------------------------- personas


async def test_a_persona_changes_name_greeting_and_faq_answer(manager: CallManager) -> None:
    manager.deps.llm._interpret = lambda user, schema: {  # type: ignore[attr-defined]
        "intent": "question",
        "question": "Who are you? Are you a robot?",
    }
    state = await manager.begin(language="en", persona="demo-residential")
    assert state["persona"] == "demo-residential"
    assert state["bot_messages"][0].startswith("Hi, this is Ava")
    state = await manager.message(state["call_id"], "Who are you? Are you a robot?")
    assert "I'm Ava" in state["bot_messages"][-1]


async def test_without_persona_the_language_templates_are_used(manager: CallManager) -> None:
    state = await manager.begin(language="en")
    with i18n.use("en"):
        assert state["bot_messages"][0] == render("identify_estate.greeting")


async def test_unknown_persona_fails_early(manager: CallManager) -> None:
    with pytest.raises(personas.UnknownPersona):
        await manager.begin(persona="nobody")


def test_persona_templates_keep_the_template_contract() -> None:
    from building_call.graph import templates as tpl

    for persona in personas.personas().values():
        assert set(persona.languages) <= set(i18n.supported())
        for language in persona.languages.values():
            for id_, text in language.templates.items():
                assert tpl.validate(id_, text) == [], (persona.id, id_)


async def test_phrasing_gets_the_persona_name_and_tone() -> None:
    from building_call.llm.phrasing import phrase

    seen: list[str] = []

    class Phraser:
        name = "fake"

        async def extract(self, system: str, user: str, schema: Any, **_: Any) -> dict[str, Any]:
            seen.append(system)
            return {"reply": "Got it. Which apartment are you in?"}

    with i18n.use("en"), personas.use("demo-residential"):
        await phrase(Phraser(), "Which apartment are you in?", "hello", {})
    assert "Your name is Ava" in seen[0]


# ------------------------------------------------------------------------------- providers


class _Voice:
    def __init__(self, name: str, rate: int, fails: bool = False) -> None:
        self.name, self.rate, self.fails = name, rate, fails

    def available(self) -> bool:
        return True

    async def synthesize(self, text: str, language: str | None = None) -> bytes:
        if self.fails:
            raise RuntimeError("down")
        return b"\x01\x00" * 8


async def test_tts_chain_picks_a_provider_that_speaks_the_language() -> None:
    from building_call.telephony.tts import FallbackSynthesizer

    urdu_capable = _Voice("eleven", 16000)
    local = _Voice("piper", 16000)
    chain = FallbackSynthesizer(
        [(local, frozenset({"es", "en"})), (urdu_capable, frozenset({"es", "en", "ur"}))]
    )
    assert chain.chain("ur") == [urdu_capable] and chain.chain("es")[0] is local
    assert chain.speaks("ur") and not chain.speaks("hi")
    assert await chain.synthesize("hola", "es")


async def test_tts_chain_falls_back_and_resamples() -> None:
    from building_call.telephony.tts import FallbackSynthesizer, UnsupportedLanguage

    broken = _Voice("eleven", 16000, fails=True)
    local = _Voice("piper", 22050)
    chain = FallbackSynthesizer([(broken, frozenset({"ca"})), (local, frozenset({"ca"}))])
    pcm = await chain.synthesize("hola", "ca")
    assert pcm and len(pcm) % 2 == 0  # 22.05 kHz resampled to 16 kHz
    with pytest.raises(UnsupportedLanguage):
        await chain.synthesize("hola", "ur")


def test_auto_tts_with_nothing_configured_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from building_call.telephony.tts import create_tts

    monkeypatch.setattr(settings, "tts", "auto")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "piper_model", "")
    monkeypatch.setattr(settings, "piper_dir", "")
    monkeypatch.setattr(settings, "cloudflare_account_id", "")
    assert not create_tts(settings).available()


async def test_stt_session_listens_to_every_language_until_one_is_chosen() -> None:
    from urllib.parse import parse_qs, urlparse

    from building_call.stt_streaming import AssemblyAIStreamingSession

    session = AssemblyAIStreamingSession(
        "key",
        language=["es", "en", "ar"],
        language_detection=True,
        speech_model="universal-3-6-pro",
    )
    query = parse_qs(urlparse(session._url).query)
    assert json.loads(query["language_codes"][0]) == ["es", "en", "ar"]
    assert query["language_detection"] == ["true"]
    assert query["speech_model"] == ["universal-3-6-pro"]

    sent: list[str] = []

    class Ws:
        async def send(self, message: str) -> None:
            sent.append(message)

    session._ws = Ws()
    await session.set_language("ar")
    assert json.loads(sent[0]) == {"type": "UpdateConfiguration", "language_codes": ["ar"]}
    await session.set_language("ar")
    assert len(sent) == 1  # nothing to change


# ------------------------------------------------------------------------------- formats


@pytest.mark.parametrize(
    ("code", "day", "amount"),
    [
        ("en", "Nov 24, 2026", "$1,250.50"),
        ("es", "24 nov 2026", "1.250,50\xa0€"),
        ("hi", "24 नव॰ 2026", "₹1,250.50"),
    ],
)
def test_dates_and_money_follow_the_locale(code: str, day: str, amount: str) -> None:
    assert format_date(date(2026, 11, 24), code) == day
    assert format_currency(1250.5, code) == amount


def test_indian_digit_grouping() -> None:
    assert format_number(125000, "hi") == "1,25,000"
    assert format_number(125000, "en") == "125,000"
