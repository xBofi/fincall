"""Delivery ledger: which part of each message the caller heard and what the next turn does."""

from __future__ import annotations

from typing import Any

from building_call.graph.ledger import evaluate, split_pieces
from building_call.services import CallManager

GREETING = (
    "Hi, you've reached FinCall, your building's AI assistant. This call may be "
    "recorded. Which building are you calling about?"
)
SUMMARY_START = "Let me check I've got this right: "


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


def _last_interpretation(llm: Any) -> str:
    return [user for title, user in llm.calls if title == "interpretation"][-1]


async def _cut(manager: CallManager, state: dict, heard_text: str) -> None:
    """Like the browser: the turn's message played up to `heard_text` and the caller cut it."""
    text = state["last_bot_message"]
    assert text.startswith(heard_text)
    await manager.record_delivery(
        state["call_id"], state["turn"], "interrupted", len(heard_text) / len(text), heard_text
    )


# ----------------------------------------------------------------------------- pieces


def test_split_pieces_splits_by_sentence_and_marks_importance() -> None:
    notices = [
        "Hi, you've reached FinCall, your building's AI assistant. This call may be recorded."
    ]
    pieces = split_pieces("Perfect. " + GREETING, notices)
    assert [p["importance"] for p in pieces] == ["courtesy", "notice", "notice", "question"]
    assert pieces[3]["text"] == "Which building are you calling about?"
    # What follows the last question is part of it
    still = split_pieces("Is that the right building? Please say yes or no.", [])
    assert [p["importance"] for p in still] == ["question", "question"]
    text = "Perfect. " + GREETING
    assert all(text[p["start"] : p["end"]] == p["text"] for p in pieces)


def test_evaluate_cut_mid_piece() -> None:
    text = "The patio faucet in the courtyard is dripping. Is that correct?"
    pieces = split_pieces(text, [])
    # Hears "The patio faucet in" (4 of 8 words): under 60 %, counts as not heard
    evaluated = evaluate(pieces, len("The patio faucet in"))
    assert [p["state"] for p in evaluated] == ["not_heard", "not_delivered"]
    assert evaluated[0]["heard_text"] == "The patio faucet in"
    # With "...in the" it is 5 of 8: heard
    evaluated = evaluate(pieces, len("The patio faucet in the"))
    assert evaluated[0]["state"] == "heard"
    assert evaluate(pieces, len(text))[1]["state"] == "delivered"


# ----------------------------------------------------------------------------- the next turn


async def test_yes_after_unheard_summary_does_not_confirm_and_repeats(manager: CallManager) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "1 A", "yes", "The faucet is dripping"]
    )
    summary = state["last_bot_message"]
    assert summary.startswith(SUMMARY_START)
    await _cut(manager, state, SUMMARY_START)
    state = await manager.message(state["call_id"], "yes")
    assert state["current_node"] == "confirm_summary"
    assert state.get("confirmed") is not True
    assert summary in state["bot_messages"][-1]


async def test_yes_over_the_end_of_the_question_confirms(manager: CallManager) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "1 A", "yes", "The faucet is dripping"]
    )
    text = state["last_bot_message"]
    await _cut(manager, state, text[: text.rindex("Is that") + 2])  # "Is" and they say yes
    state = await manager.message(state["call_id"], "yes")
    assert state["result"] == "incident_created"


async def test_unheard_estate_question_and_bare_yes_repeats(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street"])
    await _cut(manager, state, "I have ")
    state = await manager.message(state["call_id"], "yes")
    assert not state.get("estate_id")
    assert "I have 12 Main Street" in state["bot_messages"][-1]


async def test_unheard_notice_is_said_before_the_answer(manager: CallManager) -> None:
    state = await manager.begin()
    await _cut(manager, state, "Hi, you've reached FinCall")
    state = await manager.message(state["call_id"], "12 Main Street")
    said = " ".join(state["bot_messages"])
    assert "AI assistant" in said and "recorded" in said
    assert said.index("recorded") < said.index("I have 12 Main Street")
    # Already said: it is not put in front again on the next turn
    state = await manager.message(state["call_id"], "yes")
    assert "recorded" not in " ".join(state["bot_messages"])


async def test_unheard_courtesy_changes_nothing(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", "yes"])
    text = state["last_bot_message"]
    assert text.startswith("Perfect. ")
    await manager.record_delivery(state["call_id"], state["turn"], "completed", 1.0, text)
    state = await manager.message(state["call_id"], "The faucet is dripping")
    assert state["current_node"] == "confirm_summary"


async def test_the_model_knows_what_the_caller_heard(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", "yes"])
    await manager.record_delivery(
        state["call_id"], state["turn"], "completed", 1.0, state["last_bot_message"]
    )
    await manager.message(state["call_id"], "The patio faucet is dripping")
    assert "they only heard" not in _last_interpretation(llm)

    state = await _converse(manager, ["12 Main Street", "yes", "1 A", "yes"])
    await _cut(manager, state, "Perfect. So, wha")
    await manager.message(state["call_id"], "The patio faucet is dripping")
    assert 'they only heard: "Perfect. So, wha"' in _last_interpretation(llm)


async def test_delivery_records_the_pieces(manager: CallManager) -> None:
    state = await manager.begin()
    await _cut(manager, state, "Hi, you've reached FinCall")
    pieces = (await manager.get(state["call_id"]))["last_delivery"]["pieces"]
    assert {p["importance"] for p in pieces} >= {"notice", "question"}
    assert pieces[-1]["state"] == "not_delivered"


async def test_yes_with_data_after_unheard_summary_does_not_confirm(
    manager: CallManager, llm: Any
) -> None:
    # With Gemini, telling the prompt what was heard was not enough: it returned confirms=true
    # (2026-10-21)
    state = await _converse(
        manager, ["12 Main Street", "yes", "1 A", "yes", "The faucet is dripping"]
    )
    await _cut(manager, state, SUMMARY_START)
    original = llm._interpret
    llm._interpret = lambda user, schema: {  # type: ignore[method-assign]
        "confirms": True,
        "problem": "water is coming through the ceiling",
    }
    state = await manager.message(
        state["call_id"], "Yes, yes, and now water is coming through the ceiling too"
    )
    llm._interpret = original  # type: ignore[method-assign]
    assert state.get("result") != "incident_created"
    assert state["current_node"] == "confirm_summary"
