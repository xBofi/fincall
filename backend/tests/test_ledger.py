"""Ledger de entrega: qué parte de cada mensaje oyó el vecino y qué hace el turno siguiente."""

from __future__ import annotations

from typing import Any

from building_call.graph.ledger import evaluate, split_pieces
from building_call.services import CallManager

GREETING = (
    "Hola, le atiende un asistente de inteligencia artificial de su comunidad. Esta llamada "
    "puede ser grabada. ¿En qué finca se encuentra?"
)


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


def _last_interpretation(llm: Any) -> str:
    return [user for title, user in llm.calls if title == "interpretation"][-1]


async def _cut(manager: CallManager, state: dict, heard_text: str) -> None:
    """Como el navegador: el mensaje del turno sonó hasta `heard_text` y el vecino lo cortó."""
    text = state["last_bot_message"]
    assert text.startswith(heard_text)
    await manager.record_delivery(
        state["call_id"], state["turn"], "interrupted", len(heard_text) / len(text), heard_text
    )


# ----------------------------------------------------------------------------- piezas


def test_split_pieces_splits_by_sentence_and_marks_importance() -> None:
    notices = [
        "Hola, le atiende un asistente de inteligencia artificial de su comunidad. "
        "Esta llamada puede ser grabada."
    ]
    pieces = split_pieces("Perfecto. " + GREETING, notices)
    assert [p["importance"] for p in pieces] == ["courtesy", "notice", "notice", "question"]
    assert pieces[3]["text"] == "¿En qué finca se encuentra?"
    # Lo que sigue a la última pregunta es parte de ella
    still = split_pieces("¿Es correcta esa finca? Responda sí o no, por favor.", [])
    assert [p["importance"] for p in still] == ["question", "question"]
    text = "Perfecto. " + GREETING
    assert all(text[p["start"] : p["end"]] == p["text"] for p in pieces)


def test_evaluate_cut_mid_piece() -> None:
    text = "Le resumo: gotea el grifo del patio comunitario. ¿Es correcto?"
    pieces = split_pieces(text, [])
    # Oye "Le resumo: gotea el" (4 de 8 palabras): menos del 60 %, cuenta como no oída
    evaluated = evaluate(pieces, len("Le resumo: gotea el"))
    assert [p["state"] for p in evaluated] == ["not_heard", "not_delivered"]
    assert evaluated[0]["heard_text"] == "Le resumo: gotea el"
    # Con "...el grifo" ya son 5 de 8: oída
    evaluated = evaluate(pieces, len("Le resumo: gotea el grifo"))
    assert evaluated[0]["state"] == "heard"
    assert evaluate(pieces, len(text))[1]["state"] == "delivered"


# ----------------------------------------------------------------------------- el turno siguiente


async def test_yes_after_unheard_summary_does_not_confirm_and_repeats(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    summary = state["last_bot_message"]
    assert summary.startswith("Le resumo")
    await _cut(manager, state, "Le resumo: ")
    state = await manager.message(state["call_id"], "sí")
    assert state["current_node"] == "confirm_summary"
    assert state.get("confirmed") is not True
    assert summary in state["bot_messages"][-1]


async def test_yes_over_the_end_of_the_question_confirms(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    text = state["last_bot_message"]
    await _cut(manager, state, text[: text.index("¿") + 3])  # "¿Es" y el vecino ya dice sí
    state = await manager.message(state["call_id"], "sí")
    assert state["result"] == "incident_created"


async def test_unheard_estate_question_and_bare_yes_repeats(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12"])
    await _cut(manager, state, "He identificado ")
    state = await manager.message(state["call_id"], "sí")
    assert not state.get("estate_id")
    assert "He identificado" in state["bot_messages"][-1]


async def test_unheard_notice_is_said_before_the_answer(manager: CallManager) -> None:
    state = await manager.begin()
    await _cut(manager, state, "Hola, le atiende un asistente")
    state = await manager.message(state["call_id"], "Calle Mayor 12")
    said = " ".join(state["bot_messages"])
    assert "inteligencia artificial" in said and "grabada" in said
    assert said.index("grabada") < said.index("He identificado")
    # Ya se ha dicho: no se vuelve a anteponer en el turno siguiente
    state = await manager.message(state["call_id"], "sí")
    assert "grabada" not in " ".join(state["bot_messages"])


async def test_unheard_courtesy_changes_nothing(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "sí"])
    text = state["last_bot_message"]
    assert text.startswith("Perfecto. ")
    await manager.record_delivery(state["call_id"], state["turn"], "completed", 1.0, text)
    state = await manager.message(state["call_id"], "Gotea el grifo")
    assert state["current_node"] == "confirm_summary"


async def test_the_model_knows_what_the_caller_heard(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "sí"])
    await manager.record_delivery(
        state["call_id"], state["turn"], "completed", 1.0, state["last_bot_message"]
    )
    await manager.message(state["call_id"], "Gotea el grifo del patio")
    assert "solo llegó a oír" not in _last_interpretation(llm)

    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "sí"])
    await _cut(manager, state, "Perfecto. Cuéntem")
    await manager.message(state["call_id"], "Gotea el grifo del patio")
    assert 'solo llegó a oír: "Perfecto. Cuéntem"' in _last_interpretation(llm)


async def test_delivery_records_the_pieces(manager: CallManager) -> None:
    state = await manager.begin()
    await _cut(manager, state, "Hola, le atiende un asistente")
    pieces = (await manager.get(state["call_id"]))["last_delivery"]["pieces"]
    assert {p["importance"] for p in pieces} >= {"notice", "question"}
    assert pieces[-1]["state"] == "not_delivered"


async def test_yes_with_data_after_unheard_summary_does_not_confirm(
    manager: CallManager, llm: Any
) -> None:
    # Con Gemini, el aviso de lo oído en el prompt no basta: devolvía confirma=true (2026-10-21)
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    await _cut(manager, state, "Le resumo: ")
    original = llm._interpret
    llm._interpret = lambda user, schema: {  # type: ignore[method-assign]
        "confirms": True,
        "problem": "sale agua por el techo",
    }
    state = await manager.message(
        state["call_id"], "Sí, sí, y además ahora sale agua por el techo"
    )
    llm._interpret = original  # type: ignore[method-assign]
    assert state.get("result") != "incident_created"
    assert state["current_node"] == "confirm_summary"
