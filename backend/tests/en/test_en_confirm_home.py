"""Read-back of building and apartment (`confirm_home`) before asking about the problem.

A misheard floor or letter is caught right after the apartment, not after the whole report.
"""

from __future__ import annotations

from typing import Any

from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager

START = ["12 Main Street", "yes", "1 A"]
READ_BACK_1A = "So that's 12 Main Street, apartment 1A. Is that right?"


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


def _templates(state: dict) -> list[str]:
    return [t["detail"].split(" ")[0] for t in state["trace"] if t["action"] == "template"]


async def test_the_apartment_is_read_back_with_the_building(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, START)
    assert state["home_id"] == "estate-main-12-u-1A"
    assert state["current_node"] == "confirm_home"
    assert state["awaiting_user"] is True
    assert state["bot_messages"][-1] == READ_BACK_1A
    assert not state.get("home_confirmed")
    assert llm.calls == []  # address, yes and apartment are all deterministic


async def test_yes_to_the_read_back_asks_for_the_problem(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, [*START, "yes"])
    assert state["home_confirmed"] is True
    assert "home_confirmed_verbally" in state["validations"]
    assert state["current_node"] == "collect_request"
    assert state["bot_messages"][-1] == "Perfect. So, what's the problem?"
    assert llm.calls == []


async def test_no_with_the_right_apartment_reads_the_new_one_back(
    manager: CallManager, llm: Any
) -> None:
    state = await _converse(manager, START)
    state = await manager.message(state["call_id"], "No, it's 3A")
    assert llm.calls == []  # short "no" + apartment by format: no model
    assert state["home_id"] == "estate-main-12-u-3A"
    assert state["current_node"] == "confirm_home"
    assert state["bot_messages"][-1] == "So that's 12 Main Street, apartment 3A. Is that right?"
    assert not state.get("home_confirmed")
    state = await manager.message(state["call_id"], "yes")
    assert state["home_confirmed"] is True
    assert state["home_id"] == "estate-main-12-u-3A"
    assert state["current_node"] == "collect_request"


async def test_plain_no_asks_the_apartment_again(manager: CallManager) -> None:
    state = await _converse(manager, [*START, "no"])
    assert state["current_node"] == "identify_home"
    assert state["home_id"] is None
    assert state["bot_messages"][-1] == "Sorry about that. What's your floor and apartment letter?"
    assert "confirm_home.wrong" in _templates(state)
    state = await manager.message(state["call_id"], "2 B")
    assert state["home_id"] == "estate-main-12-u-2B"
    assert state["current_node"] == "confirm_home"
    assert "apartment 2B" in state["bot_messages"][-1]


async def test_answering_with_the_problem_accepts_the_read_back(
    manager: CallManager, repo: MemoryRepository
) -> None:
    problem = "There's a water leak in the bathroom and it's flooding the floor"
    state = await _converse(manager, [*START, problem])
    assert state["home_confirmed"] is True
    assert state["home_id"] == "estate-main-12-u-1A"
    assert state["request"] == problem
    assert state["category"] == "plumbing"
    assert state["current_node"] == "confirm_summary"  # "what's the problem?" is not asked
    assert not any("what's the problem" in m.lower() for m in state["bot_messages"])
    state = await manager.message(state["call_id"], "yes")
    assert state["result"] == "incident_created"
    assert len(await repo.list_incidents()) == 1


async def test_problem_said_before_the_read_back_is_used_after_yes(
    manager: CallManager,
) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "1 A, there's a water leak in the bathroom", "yes"]
    )
    assert state["home_confirmed"] is True
    assert state["request"] == "1 A, there's a water leak in the bathroom"
    assert state["current_node"] == "confirm_summary"


async def test_apartment_correction_while_asking_the_problem_is_read_back_again(
    manager: CallManager,
) -> None:
    state = await _converse(manager, [*START, "yes"])
    assert state["current_node"] == "collect_request"
    state = await manager.message(state["call_id"], "Sorry, it's actually 3 B")
    assert state["home_id"] == "estate-main-12-u-3B"
    assert not state.get("home_confirmed")
    assert state["current_node"] == "confirm_home"
    assert state["bot_messages"][-1] == "So that's 12 Main Street, apartment 3B. Is that right?"
    assert not state.get("request")  # the correction is not filed as the problem
    state = await manager.message(state["call_id"], "yes")
    assert state["home_confirmed"] is True
    assert state["current_node"] == "collect_request"


async def test_an_emergency_skips_the_read_back(
    manager: CallManager, repo: MemoryRepository
) -> None:
    repo.administrator_config["adm-demo"] = repo.administrator_config["adm-demo"].model_copy(
        update={"emergencies_enabled": True}
    )
    state = await _converse(
        manager,
        ["12 Main Street, there's a smell of gas on the landing", "yes", "third floor, B"],
    )
    assert state["home_id"] == "estate-main-12-u-3B"
    assert not any(t["node"] == "confirm_home" for t in state["trace"])
    assert "confirm_home.ask" not in _templates(state)
    assert any(t["node"] == "handle_emergency" for t in state["trace"])


async def test_building_candidate_answered_with_the_problem_is_accepted(
    manager: CallManager,
) -> None:
    state = await _converse(manager, ["12 Main Street", "There's a water leak in the bathroom"])
    assert state["estate_id"] == "estate-main-12"
    assert state["estate_attempts"] == 0
    assert "estate_confirmed_verbally" in state["validations"]
    assert state["current_node"] == "identify_home"
    state = await manager.message(state["call_id"], "1 A")
    assert state["current_node"] == "confirm_home"
    state = await manager.message(state["call_id"], "yes")
    # The problem told at the building question is not asked again
    assert state["request"] == "There's a water leak in the bathroom"
    assert state["current_node"] == "confirm_summary"


# --------------------------------------------------- suspected bugs in the new code (2026-11-16)


async def test_short_problem_with_isnt_answering_the_read_back_is_accepted(
    manager: CallManager,
) -> None:
    """`_moves_on_to_problem` says "isn't working" is about the problem, but `_interpret`'s
    deterministic yes/no shortcut (<= 4 words, "isnt" in `_NO`) decides it as a "no" first."""
    state = await _converse(manager, [*START, "The intercom isn't working"])
    assert state["current_node"] != "identify_home"
    assert state["home_confirmed"] is True
    assert state["request"] == "The intercom isn't working"


async def test_short_problem_with_isnt_answering_the_building_question_is_accepted(
    manager: CallManager,
) -> None:
    """Same shortcut at the building candidate: the building is rejected and an attempt spent."""
    state = await _converse(manager, ["12 Main Street", "The intercom isn't working"])
    assert state["estate_id"] == "estate-main-12"
    assert state["estate_attempts"] == 0


async def test_problem_with_no_in_it_answering_the_read_back_is_accepted(
    manager: CallManager,
) -> None:
    """ "There's no light in the stairwell" is a problem, not a refusal: it accepts the read-back."""
    state = await _converse(manager, [*START, "There's no light in the stairwell"])
    assert state["home_confirmed"] is True
    assert state["request"] == "There's no light in the stairwell"
    assert state["current_node"] == "confirm_summary"
