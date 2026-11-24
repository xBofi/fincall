from __future__ import annotations

from building_call.graph.registry import REGISTRY, get_graph
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


async def test_initial_greeting_asks_for_estate(manager: CallManager) -> None:
    state = await manager.begin()
    assert state["current_node"] == "identify_estate"
    assert state["awaiting_user"] is True
    assert "building" in state["bot_messages"][0].lower()


async def test_full_flow_creates_incident(manager: CallManager, repo: MemoryRepository) -> None:
    state = await _converse(
        manager,
        [
            "I live at number 12 Main Street",
            "yes",
            "third floor, B",
            "There's a water leak in the bathroom and it's flooding the floor",
            "yes, correct",
        ],
    )
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert state["estate_id"] == "estate-main-12"
    assert state["home_id"] == "estate-main-12-u-3B"
    assert "declared_name" not in state  # the name is neither asked nor kept
    assert state["category"] == "plumbing"
    assert state["urgency"] == "high"
    assert state["specialist_id"] == "spc-plumber"
    assert state["notice_channel"] == "email"  # default rule for high urgency
    incidents = await repo.list_incidents()
    assert len(incidents) == 1 and incidents[0].state == "assigned"
    call = repo.calls[state["call_id"]]
    assert call.config_id == "cfg-main-12-v1"
    assert call.graph == "standard"
    assert "data_validated" in state["validations"]


async def test_estate_not_identified_after_three_attempts(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager, ["99 Madeup Street", "Some other odd street", "Nowhere Square"]
    )
    assert state["current_node"] == "estate_not_identified"
    assert state["awaiting_user"] is True
    state = await manager.message(state["call_id"], "I wanted to report a broken streetlight")
    assert state["finished"] is True
    assert state["result"] == "estate_not_identified"
    assert repo.calls[state["call_id"]].result == "estate_not_identified"
    assert await repo.list_incidents() == []


async def test_rejecting_candidate_estate_counts_as_an_attempt(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "no"])
    assert state["current_node"] == "identify_estate"
    assert state["estate_attempts"] == 1
    assert state["estate_id"] is None


async def test_home_not_identified_respects_the_estate_attempts(
    manager: CallManager, repo: MemoryRepository
) -> None:
    # 5 Elm Avenue has home_attempts=4 in its configuration
    state = await _converse(manager, ["5 Elm Avenue", "yes", "ninth Z", "ninth Z", "ninth Z"])
    assert state["current_node"] == "identify_home"
    assert state["home_attempts"] == 3
    state = await manager.message(state["call_id"], "ninth Z")
    assert state["current_node"] == "home_not_identified"
    state = await manager.message(state["call_id"], "The garage door is broken")
    assert state["result"] == "home_not_identified"
    assert state["notice_target"] == "elm5@demo.test"
    assert await repo.list_incidents() == []


async def test_home_with_entrance(manager: CallManager) -> None:
    state = await _converse(manager, ["5 Elm Avenue", "yes", "entrance 2, second floor C"])
    assert state["home_id"] == "estate-elm-5-2-2C"
    # Building and apartment are read back together once, before the problem
    assert state["current_node"] == "confirm_home"
    assert state["bot_messages"][-1] == (
        "So that's 5 Elm Avenue, entrance 2, apartment 2C. Is that right?"
    )
    state = await manager.message(state["call_id"], "yes")
    assert state["current_node"] == "collect_request"
    assert state["home_confirmed"] is True
    # Without asking the name or repeating the apartment again: straight to the issue
    assert state["bot_messages"][-1] == "Perfect. So, what's the problem?"


async def test_rejecting_summary_goes_back_to_collect_request(manager: CallManager) -> None:
    state = await _converse(
        manager,
        ["12 Main Street", "yes", "1 A", "yes", "There's no light in the stairwell", "no"],
    )
    assert state["current_node"] == "collect_request"
    assert state["confirmed"] is False
    state = await manager.message(state["call_id"], "The elevator is stuck with people inside")
    assert state["category"] == "elevator"
    assert state["current_node"] == "confirm_summary"


async def test_unclassifiable_creates_pending_incident(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager, ["12 Main Street", "yes", "2 B", "I saw a UFO on the roof", "yes"]
    )
    # We stand in for the property manager: even if it is not classified, the issue is created
    # and stays pending for them
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert "no_category->pending_administrator" in state["validations"]
    assert state["specialist_id"] is None
    incidents = await repo.list_incidents()
    assert len(incidents) == 1 and incidents[0].state == "pending"
    assert "property manager will review it" in state["bot_messages"][-1]


async def test_category_without_specialist_stays_pending_for_the_administrator(
    manager: CallManager,
) -> None:
    # "cleaning" is in the catalog but the demo property manager has no contractor for it
    state = await _converse(
        manager,
        [
            "12 Main Street",
            "yes",
            "2 B",
            "yes",
            "The trash at the entrance hasn't been collected for days",
            "yes",
        ],
    )
    assert state["result"] == "incident_created"
    assert "no_specialist->pending_administrator" in state["validations"]
    assert state["notice_target"] == "main12@demo.test"


async def test_message_after_finishing_does_not_change_state(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes", "2 B", "I saw a UFO", "yes"])
    again = await manager.message(state["call_id"], "hello?")
    assert again == state


def test_registry_only_has_the_standard_graph(deps) -> None:
    assert sorted(REGISTRY) == ["standard"]
    assert get_graph("standard", deps) is not None


async def test_history_contains_bot_and_caller_turns(manager: CallManager) -> None:
    state = await _converse(manager, ["12 Main Street", "yes"])
    roles = [t["role"] for t in state["history"]]
    assert roles == ["bot", "caller", "bot", "caller", "bot"]
    assert state["history"][0]["text"].startswith("Hi")


async def test_the_summary_does_not_read_the_category(manager: CallManager) -> None:
    """The category is internal: the caller hears what they said, not "plumbing" (2026-10-21)."""
    state = await _converse(
        manager,
        [
            "I live at number 12 Main Street",
            "yes",
            "third floor, B",
            "There's a water leak in the bathroom and it's flooding the floor",
        ],
    )
    summary = " ".join(state["bot_messages"])
    assert "water leak" in summary
    assert "plumbing" not in summary.lower()


async def test_the_closing_keeps_the_professional_capitalisation(manager: CallManager) -> None:
    state = await _converse(
        manager,
        [
            "I live at number 12 Main Street",
            "yes",
            "third floor, B",
            "There's a water leak in the bathroom and it's flooding the floor",
            "yes, correct",
        ],
    )
    closing = " ".join(state["bot_messages"])
    assert "smith plumbing" not in closing and "Smith Plumbing" in closing
