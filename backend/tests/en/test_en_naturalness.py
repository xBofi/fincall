"""The caller talks their own way; the state machine adapts without losing control."""

from __future__ import annotations

from typing import Any

import pytest

from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


async def test_everything_in_one_sentence_skips_already_answered_questions(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager,
        [
            "Hi, I'm calling from 5 Elm Avenue, entrance 2, third floor A. My name is Carmen "
            "and there's a water leak in the bathroom",
            "yes",
        ],
    )
    # After confirming the building it already knows the apartment: it reads both back once
    assert state["current_node"] == "confirm_home"
    assert state["bot_messages"][-1] == (
        "So that's 5 Elm Avenue, entrance 2, apartment 3A. Is that right?"
    )
    state = await manager.message(state["call_id"], "yes")
    # The problem was already said: straight to the summary
    assert state["current_node"] == "confirm_summary"
    assert state["home_id"] == "estate-elm-5-2-3A"
    assert "declared_name" not in state and "name" not in state["interpretation"]
    assert state["category"] == "plumbing"
    questions = [h["text"] for h in state["history"] if h["role"] == "bot"]
    assert not any("Which apartment are you in?" in p for p in questions)
    assert not any("your name" in p for p in questions)
    state = await manager.message(state["call_id"], "yes")
    assert state["result"] == "incident_created"
    assert len(await repo.list_incidents()) == 1


async def test_loose_data_accumulates_and_is_used_later(manager: CallManager) -> None:
    state = await _converse(
        manager,
        [
            "12 Main Street",
            "yes",
            "Fourth floor, A. My name is Pedro and there's no light in the stairwell",
            "yes",
        ],
    )
    assert state["home_id"] == "estate-main-12-u-4A"
    assert "declared_name" not in state and "name" not in state["interpretation"]
    assert state["request"]
    assert state["current_node"] == "confirm_summary"


async def test_asking_to_repeat_repeats_the_question(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "What? I didn't hear you"])
    assert state["current_node"] == "identify_home"
    assert "say that again." in state["bot_messages"][-1]
    assert "apartment" in state["bot_messages"][-1]


async def test_caller_question_is_answered_and_resumed(manager: CallManager) -> None:
    state = await _converse(manager, ["Is this call being recorded?"])
    assert state["current_node"] == "identify_estate"
    assert "is recorded" in state["bot_messages"][-1]
    assert "building" in state["bot_messages"][-1]
    assert state["estate_attempts"] == 0  # does not count as a failed attempt


async def test_out_of_scope_explains_and_resumes(manager: CallManager) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "2 B", "yes", "How much do I pay in fees each month?"]
    )
    assert state["current_node"] == "collect_request"
    assert "i can only help with maintenance issues" in state["bot_messages"][-1].lower()


async def test_asking_for_a_person_hands_off_to_the_administrator(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "I want to speak to a person"])
    assert state["finished"] is True
    assert state["result"] == "handed_to_administrator"
    assert state["notice_target"] == "main12@demo.test"
    assert repo.calls[state["call_id"]].result == "handed_to_administrator"


async def test_farewell_closes_the_call(manager: CallManager, repo: MemoryRepository) -> None:
    state = await _converse(manager, ["Goodbye, I'll call back another time"])
    assert state["finished"] is True
    assert state["result"] == "abandoned"
    assert await repo.list_incidents() == []


async def test_rejecting_candidate_with_another_address_does_not_use_an_attempt(
    manager: CallManager,
) -> None:
    state = await _converse(manager, ["12 Main Street", "No, no, 5 Elm Avenue"])
    assert state["candidate_estate_id"] == "estate-elm-5"
    assert state["estate_attempts"] == 0
    assert "5 Elm Avenue" in state["bot_messages"][-1]


async def test_correction_in_the_summary_reclassifies(manager: CallManager) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "3 B", "yes", "There's no light in the stairwell"]
    )
    assert state["category"] == "electrical"
    state = await manager.message(
        state["call_id"], "No, I made a mistake, it's a water leak in the bathroom"
    )
    assert state["category"] == "plumbing"
    assert state["current_node"] == "confirm_summary"
    assert state["bot_messages"][0] == "Okay, I'll correct that."


async def test_home_correction_in_the_summary(manager: CallManager) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "3 B", "yes", "There's no light in the stairwell"]
    )
    state = await manager.message(state["call_id"], "No, earlier I said 3 B but it's 3 A")
    assert state["home_id"] == "estate-main-12-u-3A"
    # The corrected apartment is read back again before going back to the summary
    assert state["current_node"] == "confirm_home"
    assert "apartment 3A" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "yes")
    assert state["home_confirmed"] is True
    assert state["current_node"] == "confirm_summary"


async def test_deterministic_turns_do_not_call_the_llm(manager: CallManager, llm: Any) -> None:
    await _converse(manager, ["12 Main Street", "yes", "3 B"])
    # short address, yes/no and apartment by regex: zero model calls
    assert llm.calls == []
    aggregates = manager.metrics.aggregates()
    assert aggregates["turns"] == 4
    assert aggregates["server_ms_without_llm"]["n"] == 4


async def test_metrics_record_llm_calls_and_timings(manager: CallManager, llm: Any) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "3 B", "yes", "There's no light in the stairwell"]
    )
    turns = manager.metrics.turns(state["call_id"])
    last = turns[-1]
    assert last.llm_calls == 1  # interpretation merged with classification
    assert last.interpretation_origin == "llm"
    assert last.server_ms is not None
    assert state["turn_timings"]["server_ms"] >= 0
    completed_turn = manager.metrics.complete_client(
        state["call_id"], last.turn, {"response_ms": 1100, "vad_silence_ms": 500}
    )
    assert completed_turn is not None and completed_turn.rating == "target"
    assert manager.metrics.aggregates()["ratings"]["target"] == 1


async def test_unrecognisable_address_does_not_hand_off_to_human(
    manager: CallManager, llm: Any
) -> None:
    # The model gets it wrong and says "human" for a transcript that makes no sense
    original = llm._interpret
    llm._interpret = lambda user, schema: {"intent": "human"}  # type: ignore[method-assign]
    state = await _converse(manager, ["I'm at the Carraway Dorford building, Doctor Mood"])
    llm._interpret = original  # type: ignore[method-assign]
    assert state["finished"] is False
    assert state["current_node"] == "identify_estate"
    assert state["estate_attempts"] == 1
    assert "street and number again" in state["bot_messages"][-1]


async def test_nonexistent_home_explains_the_estate_layout(
    manager: CallManager,
) -> None:
    # The floor exists: only its apartments are offered
    state = await _converse(manager, ["12 Main Street", "yes", "floor 1, apartment 2"])
    assert state["current_node"] == "identify_home"
    assert "On the 1st floor the apartments are A or B" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "floor 1 apartment B")
    assert state["home_id"] == "estate-main-12-u-1B"
    # The floor does not exist either: the whole building is described
    state = await _converse(manager, ["12 Main Street", "yes", "floor 9, apartment 2"])
    assert "floors 1 to 4" in state["bot_messages"][-1]
    assert "apartments A or B" in state["bot_messages"][-1]


async def test_greeting_does_not_count_as_estate_attempt(manager: CallManager) -> None:
    state = await _converse(manager, ["Hi, good afternoon."])
    assert state["estate_attempts"] == 0
    assert state["bot_messages"][-1].startswith("Hi there.")
    state = await manager.message(state["call_id"], "5 Elm Avenue")
    assert state["candidate_estate_id"] == "estate-elm-5"


async def test_floor_and_door_in_several_entrances_asks_the_entrance(manager: CallManager) -> None:
    state = await _converse(manager, ["5 Elm Avenue", "yes", "Third floor A is my apartment"])
    assert state["current_node"] == "identify_home"
    assert state["home_attempts"] == 0
    assert "Which entrance is yours, 1 or 2?" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "entrance 1")
    assert state["home_id"] == "estate-elm-5-1-3A"


async def test_the_name_is_neither_kept_nor_read_back(manager: CallManager) -> None:
    """Design decision (2026-10-22): the caller's name is not stored. Only what they said keeps it."""
    state = await _converse(
        manager,
        ["12 Main Street", "yes", "3 B", "yes", "I'm Fulano and there's no light in the stairwell"],
    )
    # The test model leaves the name inside the problem; the real one separates it
    assert state["bot_messages"][-1].startswith(
        "Let me check I've got this right: I'm Fulano and there's no light"
    )
    assert "3B" not in state["bot_messages"][-1]
    assert "declared_name" not in state and "name" not in state["interpretation"]


async def test_closing_explains_the_next_step(manager: CallManager) -> None:
    state = await manager.begin(caller_phone="+15550200000")
    for t in ["12 Main Street", "yes", "3 B", "yes", "The patio faucet is dripping", "yes"]:
        state = await manager.message(state["call_id"], t)
    final = state["bot_messages"][-1]
    assert "Smith Plumbing" in final
    assert "will call you back at this number" in final
    assert "can't give you a time" in final.lower() or "can't confirm a time" in final.lower()
    assert "twenty-four hours" in final


async def test_home_with_fillers_uses_the_regex_and_does_not_invent_entrance(
    manager: CallManager, llm: Any
) -> None:
    original = llm._interpret
    # The model makes up an entrance in a building without entrances
    llm._interpret = lambda user, schema: {
        "entrance": "a",
        "floor": "1",
        "door": "A",
        "confirms": True,
    }  # type: ignore[method-assign]
    state = await _converse(
        manager, ["12 Main Street", "yes", "Oh, okay, sorry. Well yes, floor 1, apartment A."]
    )
    llm._interpret = original  # type: ignore[method-assign]
    assert state["home_id"] == "estate-main-12-u-1A"


async def test_comment_about_the_bot_is_not_a_correction(manager: CallManager, llm: Any) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "1 A", "yes", "The walls in the corridor need painting"]
    )
    # The summary says what was understood, not the internal category (2026-10-21)
    assert "walls in the corridor need painting" in state["bot_messages"][-1]
    assert "wasn't able" not in state["bot_messages"][-1].lower()
    original = llm._interpret
    llm._interpret = lambda user, schema: {"problem": "you managed to classify it as low urgency"}  # type: ignore[method-assign]
    state = await manager.message(
        state["call_id"], "You'll say you managed to classify it as low urgency."
    )
    llm._interpret = original  # type: ignore[method-assign]
    assert state["current_node"] == "confirm_summary"
    assert "Please say yes or no" in state["bot_messages"][-1]
    assert state["request"].startswith("The walls")


@pytest.mark.parametrize("answer", ["Yep.", "Yeah", "That's right.", "Correct"])
async def test_short_yes_forms_confirm_without_the_llm(
    manager: CallManager, llm: Any, answer: str
) -> None:
    state = await _converse(manager, ["12 Main Street", answer])
    assert state["estate_id"] == "estate-main-12"
    assert llm.calls == []


async def test_wait_filler_does_not_use_an_attempt(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "Oh, okay, hold on."])
    assert state["current_node"] == "identify_home"
    assert state["home_attempts"] == 0
    assert "take your time" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "floor 1, apartment A")
    assert state["home_id"] == "estate-main-12-u-1A"


async def test_partial_home_data_accumulates(manager: CallManager, llm: Any) -> None:
    original = llm._interpret
    llm._interpret = lambda user, schema: {
        "floor": "3"
    }  # the STT lost the apartment letter  # type: ignore[method-assign]
    state = await _converse(manager, ["5 Elm Avenue", "yes", "Third floor is my apartment"])
    assert state["home_attempts"] == 0
    assert "And the apartment letter?" in state["bot_messages"][-1]
    llm._interpret = lambda user, schema: {"entrance": "1"}  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "Entrance 1")
    assert "And the apartment letter?" in state["bot_messages"][-1]
    llm._interpret = original  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "apartment A")
    assert state["home_id"] == "estate-elm-5-1-3A"


async def test_affirmation_with_stt_noise(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["12 Main Street", "That, that's right."])
    assert state["estate_id"] == "estate-main-12"
    assert llm.calls == []


def test_empty_model_markers_are_not_data() -> None:
    from building_call.llm.interpretation import normalize_interpretation

    i = normalize_interpretation({"name": "Not specified", "floor": "3", "door": "null"})
    assert i.name is None and i.door is None and i.floor == "3"


async def test_faq_without_question_marks(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["This call is being recorded."])
    assert "is recorded" in state["bot_messages"][-1]
    assert state["estate_attempts"] == 0


async def test_detail_question_before_the_summary(detail_manager: CallManager) -> None:
    manager = detail_manager
    state = await _converse(
        manager,
        ["12 Main Street", "yes", "1 A", "yes", "The hallway light on the second floor is out"],
    )
    assert state["current_node"] == "ask_detail"
    assert "electrician" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "It's all of them in the hallway, none come on")
    assert state["current_node"] == "confirm_summary"
    assert "Detail: It's all of them in the hallway" in state["request"]
    state = await manager.message(state["call_id"], "yes")
    assert state["result"] == "incident_created"


async def test_long_description_does_not_ask_detail(detail_manager: CallManager) -> None:
    manager = detail_manager
    long = (
        "None of the hallway lights on the second floor have worked since last night, they "
        "don't come on when you flip the switch and the rest of the building has power"
    )
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", "yes", long])
    assert state["current_node"] == "confirm_summary"


async def test_repeat_returns_the_full_message_and_hello_after_unheard_message(
    manager: CallManager, llm: Any
) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "1 A", "yes", "The faucet is dripping"]
    )
    summary = state["bot_messages"][-1]
    assert summary.startswith("Let me check")
    # The browser reports that the message was cut at 20 %
    await manager.record_delivery(state["call_id"], state["turn"], "interrupted", 0.2, "Let m")
    n = len(llm.calls)
    state = await manager.message(state["call_id"], "Hello? Are you there?")
    assert len(llm.calls) == n  # deterministic
    assert state["bot_messages"][-1] == f"Sorry, let me say that again. {summary}"
    assert state["current_node"] == "confirm_summary"
    state = await manager.message(state["call_id"], "Can you repeat that?")
    assert summary in state["bot_messages"][-1]


async def test_the_name_is_not_asked(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", "yes"])
    assert state["current_node"] == "collect_request"
    assert not any("name" in m for m in state["bot_messages"])
    state = await manager.message(state["call_id"], "Why do you need my name?")
    assert "I don't need your name" in state["bot_messages"][-1]
    assert state["current_node"] == "collect_request"


async def test_name_said_unprompted_is_not_kept(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "1 A, I'm Ana"])
    assert "Ana" not in str({k: v for k, v in state.items() if k != "history"})
    assert "Ana" not in state["bot_messages"][-1]  # the read-back has no name either
    state = await manager.message(state["call_id"], "yes")
    assert "Ana" not in str({k: v for k, v in state.items() if k != "history"})
    assert state["bot_messages"][-1].startswith("Perfect. So")


async def test_cut_phrase_when_giving_home_is_not_taken_as_fault(
    manager: CallManager, llm: Any
) -> None:
    # Real call 2026-10-11: the STT heard a cut-off "First open the" and the model returned it as
    # an unclassifiable problem; the bot skipped "what's the problem?" and asked for details of
    # a breakdown that did not exist.
    original = llm._interpret
    llm._interpret = lambda user, schema: {  # type: ignore[method-assign]
        "problem": "First open the",
        "category": "unclassifiable",
    }
    state = await _converse(manager, ["12 Main Street", "yes", "First open the"])
    llm._interpret = original  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "Floor 1, apartment A.")
    assert state["home_id"] == "estate-main-12-u-1A"
    assert state["current_node"] == "confirm_home"
    state = await manager.message(state["call_id"], "yes")
    assert state["current_node"] == "collect_request"
    assert not state.get("request")


async def test_nonexistent_door_with_valid_floor_asks_only_the_door(
    manager: CallManager, llm: Any
) -> None:
    original = llm._interpret
    llm._interpret = lambda user, schema: {"floor": "1", "door": "1"}  # type: ignore[method-assign]
    state = await _converse(manager, ["12 Main Street", "yes", "The first door."])
    llm._interpret = original  # type: ignore[method-assign]
    last = state["bot_messages"][-1]
    assert "I can't find that apartment" not in last
    assert "A or B" in last
    assert state["pending"].get("floor") == "1"
    state = await manager.message(state["call_id"], "apartment A")
    assert state["home_id"] == "estate-main-12-u-1A"


async def test_repeating_twice_does_not_stack_the_tag(manager: CallManager) -> None:
    state = await _converse(
        manager,
        ["12 Main Street", "yes", "What? I didn't hear you", "What? I didn't hear you"],
    )
    assert state["bot_messages"][-1].count("say that again") == 1
    assert "apartment" in state["bot_messages"][-1]


async def test_bare_yes_to_the_address_does_not_call_the_llm(
    manager: CallManager, llm: Any
) -> None:
    # With Gemini, "Yes." to "which building?" took 1.4 s to say it was not an address
    state = await _converse(manager, ["Yes."])
    assert llm.calls == []
    assert state["current_node"] == "identify_estate"
    assert "street and number" in state["bot_messages"][-1]


async def test_greeting_announces_the_ai_and_the_recording(manager: CallManager) -> None:
    state = await manager.begin()
    greeting = state["bot_messages"][0].lower()
    assert "ai assistant" in greeting
    assert "recorded" in greeting
    # Repeating the question does not read the whole notice again
    assert state["last_question"].startswith("Which building")


# ------------------------------------------------- routing: single words do not decide
# Substring detectors hijacked real issues (2026-10-21): "when" or "takes" were answered as an
# FAQ, "repeat" as a request to repeat and "wait" as a filler.


async def test_incidents_with_faq_words_are_recorded_as_incidents(
    manager: CallManager,
) -> None:
    cases = {
        "The elevator takes ages to come when I call it": "elevator",
        "The stairwell light goes off when I go up": "electrical",
        "Water is leaking from the access panel by the mailboxes": "plumbing",
    }
    for phrase, category in cases.items():
        state = await _converse(manager, ["12 Main Street", "yes", "1 A", phrase])
        assert state["current_node"] == "confirm_summary", phrase
        assert state["request"] == phrase
        assert state["category"] == category


async def test_repeat_inside_a_fault_is_not_a_repeat_request(manager: CallManager) -> None:
    phrase = "The boiler noise keeps repeating"
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", phrase])
    assert state["current_node"] == "confirm_summary"
    assert state["request"] == phrase


async def test_wait_inside_a_fault_is_not_a_filler(manager: CallManager) -> None:
    phrase = "Wait, the elevator isn't working"
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", phrase])
    assert state["current_node"] == "confirm_summary"
    assert state["category"] == "elevator"


def test_detectors_only_accept_the_phrase_with_fillers() -> None:
    from building_call.graph.nodes import _did_not_hear, _is_filler, _is_greeting

    assert _did_not_hear("What? I didn't hear you", {})
    assert _did_not_hear("Sorry, could you repeat that?", {})
    assert not _did_not_hear("The noise repeats", {})
    assert not _did_not_hear("What the intercom says, there's no light", {})
    assert _is_filler("Oh, okay, hold on.")
    assert _is_filler("One moment, please")
    assert not _is_filler("Wait, the elevator isn't working")
    assert _is_greeting("Hi, good afternoon")
    assert not _is_greeting("Hi, the elevator is broken")


async def test_faq_is_interpreted_by_the_model_and_answered(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["Is this call being recorded?"])
    assert "is recorded" in state["bot_messages"][-1]
    assert state["estate_attempts"] == 0
    assert len(llm.calls) == 1


async def test_fault_without_element_asks_what_fails_and_reclassifies(
    detail_manager: CallManager,
) -> None:
    manager = detail_manager
    state = await _converse(manager, ["12 Main Street", "yes", "1 A", "yes", "It's stopped"])
    assert state["category"] == "unclassifiable"
    assert state["current_node"] == "ask_detail"
    assert "what exactly isn't working" in state["bot_messages"][-1].lower()
    state = await manager.message(
        state["call_id"], "The elevator, between the second and third floor"
    )
    assert state["current_node"] == "confirm_summary"
    assert state["category"] == "elevator"


async def test_cut_phrase_without_category_is_not_taken_as_fault_either(
    manager: CallManager, llm: Any
) -> None:
    # If the model returns the problem without a category, the gap was the same
    original = llm._interpret
    llm._interpret = lambda user, schema: {"problem": "First open the"}  # type: ignore[method-assign]
    state = await _converse(manager, ["12 Main Street", "yes", "First open the"])
    llm._interpret = original  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "Floor 1, apartment A.")
    assert state["current_node"] == "confirm_home"
    state = await manager.message(state["call_id"], "yes")
    assert state["current_node"] == "collect_request"
    assert not state.get("request")


# ------------------------------------------------- English formats of the apartment and answers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("apartment 3A", (None, "3", "A")),
        ("unit 3 B", (None, "3", "B")),
        ("apt 2C", (None, "2", "C")),
        ("3B", (None, "3", "B")),
        ("third floor, B", (None, "3", "B")),
        ("the 2nd floor, apartment C", (None, "2", "C")),
        ("floor 4 apartment A", (None, "4", "A")),
        ("entrance 2, 3A", ("2", "3", "A")),
        ("entrance two, third floor C", ("2", "3", "C")),
        ("ground floor", (None, "0", None)),
    ],
)
def test_apartment_formats(text: str, expected: tuple[str | None, ...]) -> None:
    from building_call.graph.nodes import _extract_home_regex

    assert _extract_home_regex(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", True),
        ("Yeah, that's it", True),
        ("Yep.", True),
        ("Correct.", True),
        ("no", False),
        ("Nope.", False),
        ("No, it's 3A", False),
        ("That's not right", False),
        ("Not that one", False),
        ("Now it works", None),
        ("The light", None),
    ],
)
def test_yes_no_in_english(text: str, expected: bool | None) -> None:
    from building_call.graph.nodes import _yes_no_answer

    assert _yes_no_answer(text) is expected


def test_spelled_out_numbers_become_digits() -> None:
    from building_call.graph.nodes import _numbers_to_digits

    assert _numbers_to_digits("twelve main street") == "12 main street"
    assert _numbers_to_digits("thirty two elm") == "32 elm"
