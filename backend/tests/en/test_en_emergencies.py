"""Special handling of emergencies (gas, fire, smoke, someone trapped).

Confirmed rule: emergencies are disabled at first and treated as `high`. With the
`emergencies_enabled` setting, the bot first gives safety advice, does not ask for detail or read
the long summary, creates the issue as an emergency and leaves the alert to the coordinator and
to the building's official phone as simulated (it never calls or sends anything).
"""

from __future__ import annotations

import pytest

from building_call.graph.nodes import Dependencies, _safety_instruction
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager, _deserves_phrasing

START = ["12 Main Street", "yes", "third floor, B"]
GAS = "There's a smell of gas on the landing"


def _activate(repo: MemoryRepository, **others: object) -> None:
    repo.administrator_config["adm-demo"] = repo.administrator_config["adm-demo"].model_copy(
        update={"emergencies_enabled": True, **others}
    )


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


def _templates(state: dict) -> list[str]:
    return [t["detail"].split(" ")[0] for t in state["trace"] if t["action"] == "template"]


# --------------------------------------------------------------------- disabled (default)


async def test_disabled_the_emergency_follows_the_high_urgency_flow(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, [*START, GAS])
    assert state["declared_urgency"] == "emergency"
    assert state["urgency"] == "high"
    assert state["current_node"] == "confirm_summary"
    said = " ".join(state["bot_messages"])
    assert "911" not in said
    state = await manager.message(state["call_id"], "yes")
    assert state["result"] == "incident_created"
    assert state["notice_channel"] == "email"  # `high` rule
    [incident] = await repo.list_incidents()
    assert incident.urgency == "high"
    assert not any(t["node"] == "handle_emergency" for t in state["trace"])


# --------------------------------------------------------------------- enabled


async def test_enabled_gas_gives_safety_instruction_and_confirms_the_minimum(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, GAS])
    assert state["urgency"] == "emergency"
    assert state["current_node"] == "handle_emergency"
    assert state["awaiting_user"] is True
    message = state["bot_messages"][-1]
    assert "911" in message
    assert "don't switch on lights" in message.lower()
    assert "12 Main Street" in message
    assert message.rstrip().endswith("?")
    # Neither the long summary nor a detail question
    assert "Let me check" not in message
    assert "handle_emergency.notice" in _templates(state)
    assert "handle_emergency.gas_safety" in _templates(state)
    # The safety advice is a notice: if the caller cuts it off, it is said again
    assert any("911" in a for a in state["turn_notices"])


async def test_enabled_confirmed_creates_emergency_incident_with_simulated_notice(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, GAS, "yes"])
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert state["confirmed"] is True
    [incident] = await repo.list_incidents()
    assert incident.urgency == "emergency"
    assert incident.suggestion and "emergency" in incident.suggestion.lower()
    assert "simulated" in incident.suggestion.lower()
    # Alert by call to the building's official phone: simulated, never real
    assert state["notice_channel"] == "call"
    assert state["notice_target"] == "+15550100001"
    notices = [t for t in state["trace"] if t["action"] == "simulated_emergency_notice"]
    assert len(notices) == 1
    assert "+15550100001" in notices[0]["detail"]
    assert "main12@demo.test" in notices[0]["detail"]  # coordinator
    closing = state["bot_messages"][-1]
    assert "911" in closing
    assert "notify_channel.emergency_close" in _templates(state)
    assert "confirm_summary" not in {t["node"] for t in state["trace"]}


async def test_enabled_does_not_ask_detail_even_if_the_estate_has_it_on(
    llm, repo: MemoryRepository
) -> None:
    _activate(repo, ask_detail=True)
    manager = CallManager(Dependencies(repo=repo, llm=llm))
    state = await _converse(manager, [*START, "It smells of gas"])
    assert state["current_node"] == "handle_emergency"
    assert state["detail_requested"] is False


async def test_enabled_fire_or_smoke_uses_the_fire_instruction(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, "Lots of smoke is coming out of the meter room"])
    assert state["current_node"] == "handle_emergency"
    assert "handle_emergency.fire_safety" in _templates(state)
    message = state["bot_messages"][-1].lower()
    assert "elevator" in message and "911" in message


async def test_enabled_trapped_person_uses_the_general_instruction(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, "The elevator is stuck and there's a lady inside"])
    assert state["current_node"] == "handle_emergency"
    assert "handle_emergency.general_safety" in _templates(state)
    state = await manager.message(state["call_id"], "yes")
    [incident] = await repo.list_incidents()
    assert incident.urgency == "emergency"
    assert incident.category == "elevator"
    assert state["notice_channel"] == "call"


async def test_enabled_if_the_caller_says_it_is_not_an_emergency_it_is_treated_as_high(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, GAS, "No, it's not an emergency"])
    assert state["urgency"] == "high"
    assert state["current_node"] == "confirm_summary"
    state = await manager.message(state["call_id"], "yes")
    [incident] = await repo.list_incidents()
    assert incident.urgency == "high"
    assert state["notice_channel"] == "email"


async def test_enabled_a_doubtful_answer_does_not_lose_the_emergency(
    manager: CallManager, repo: MemoryRepository
) -> None:
    """When in doubt it is logged: an emergency never ends up in a loop of re-asking."""
    _activate(repo)
    state = await _converse(manager, [*START, GAS, "Oh dear, oh dear, how frightening"])
    assert state["finished"] is True
    [incident] = await repo.list_incidents()
    assert incident.urgency == "emergency"
    assert any(t["action"] == "emergency_unconfirmed" for t in state["trace"])


async def test_enabled_normal_urgencies_do_not_change(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(
        manager, [*START, "There's a water leak in the bathroom and it's flooding the floor"]
    )
    assert state["urgency"] == "high"
    assert state["current_node"] == "confirm_summary"


@pytest.mark.parametrize(
    ("node", "expected"), [("handle_emergency", False), ("confirm_summary", True)]
)
def test_the_safety_instruction_is_not_paraphrased(node: str, expected: bool) -> None:
    """The phrasing only checks numbers and names: it could drop "don't switch on lights"."""
    state = {
        "current_node": node,
        "turn_timings": {"interpretation_origin": "llm"},
        "bot_messages": ["Short text. Is that right?"],
    }
    assert _deserves_phrasing(state, "whatever") is expected  # type: ignore[arg-type]


async def test_enabled_correcting_the_estate_reidentifies_without_losing_the_emergency(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, GAS, "No, it's at 5 Elm Avenue"])
    assert state["current_node"] == "identify_estate"
    assert state["estate_id"] is None
    assert state["urgency"] is None
    assert state["declared_urgency"] == "emergency"
    assert state["request"]  # what they said is not lost


async def test_enabled_without_estate_phone_uses_the_contact_phone(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    estate = repo.estates["estate-main-12"]
    repo.estates["estate-main-12"] = estate.model_copy(update={"official_phone": ""})
    state = await _converse(manager, [*START, GAS, "yes"])
    assert state["notice_channel"] == "call"
    assert state["notice_target"] == "+15550100001"  # contact from the configuration


@pytest.mark.parametrize(
    ("text", "template"),
    [
        ("There's a burning smell in the meter room", "handle_emergency.fire_safety"),
        ("The dumpster is on fire", "handle_emergency.fire_safety"),
        ("There's a gas leak", "handle_emergency.gas_safety"),
        ("A lady is trapped in the elevator", "handle_emergency.general_safety"),
    ],
)
def test_safety_instruction_by_keywords(text: str, template: str) -> None:
    assert _safety_instruction(text).template_id == template  # type: ignore[attr-defined]
