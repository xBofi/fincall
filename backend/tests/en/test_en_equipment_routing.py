"""Who each issue goes to: the equipment's maintainer, a contractor or the property manager.

Design rule (2026-10-21, Provisional): a contractor is only notified directly when it is a
repair, there is someone for it and the caller confirms without objections. Everything else is
decided by the property manager, with a suggestion.
"""

from __future__ import annotations

from typing import Any

from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager

# The last "yes" accepts the read-back of building and apartment (confirm_home)
START = ["12 Main Street", "yes", "1 A", "yes"]


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


async def _incident(repo: MemoryRepository) -> Any:
    incidents = await repo.list_incidents()
    assert len(incidents) == 1
    return incidents[0]


async def test_the_summary_reads_neither_category_nor_urgency(manager: CallManager) -> None:
    state = await _converse(manager, [*START, "The patio faucet is dripping"])
    summary = state["bot_messages"][-1]
    assert summary.startswith("Let me check")
    assert "classified" not in summary and "urgency" not in summary


async def test_a_leak_goes_to_the_plumber_as_always(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, [*START, "The patio faucet is dripping", "yes"])
    inc = await _incident(repo)
    assert inc.specialist_id == "spc-plumber" and inc.work_type == "repair"
    assert "Smith Plumbing" in state["bot_messages"][-1]


async def test_equipment_fault_goes_to_its_maintainer(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, [*START, "The intercom isn't working", "yes"])
    inc = await _incident(repo)
    assert inc.equipment_id == "eq-main12-intercom"
    assert inc.specialist_id == "spc-intercoms"
    assert "Fermax" in (inc.suggestion or "") and "EI-2024-118" in (inc.suggestion or "")
    assert "Eastside Intercoms" in state["bot_messages"][-1]


async def test_vandalism_goes_to_the_administrator_with_suggestion(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager, [*START, "Someone vandalized the intercom, they smashed it", "yes"]
    )
    inc = await _incident(repo)
    assert inc.specialist_id is None and inc.state == "pending"
    assert inc.work_type == "insurance_claim" and inc.equipment_id == "eq-main12-intercom"
    assert "insurance" in (inc.suggestion or "") and "Eastside Intercoms" in (inc.suggestion or "")
    closing = state["bot_messages"][-1]
    assert "property manager" in closing and "Eastside Intercoms" not in closing


async def test_yes_but_with_objection_goes_to_the_administrator(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager,
        [*START, "The hallway light isn't working", "Yes, but an electrician can't fix that"],
    )
    assert state["result"] == "incident_created"
    inc = await _incident(repo)
    assert inc.specialist_id is None
    assert "an electrician can't fix that" in (inc.suggestion or "")
    assert "Bright Electric" in (inc.suggestion or "")  # what the bot would have done


async def test_the_equipment_is_recognised_even_if_the_fault_is_told_before_the_estate(
    manager: CallManager, repo: MemoryRepository
) -> None:
    await _converse(
        manager, ["12 Main Street, the intercom isn't working", "yes", "1 A", "yes", "yes"]
    )
    inc = await _incident(repo)
    assert inc.equipment_id == "eq-main12-intercom" and inc.specialist_id == "spc-intercoms"


async def test_the_detail_asks_about_the_equipment_not_the_category(
    detail_manager: CallManager,
) -> None:
    state = await _converse(detail_manager, [*START, "The intercom isn't working"])
    assert state["current_node"] == "ask_detail"
    question = state["bot_messages"][-1]
    assert "intercom" in question and "electrician" not in question


async def test_an_insurance_claim_does_not_ask_detail(detail_manager: CallManager) -> None:
    state = await _converse(
        detail_manager, [*START, "Someone vandalized the intercom, they smashed it"]
    )
    assert state["current_node"] == "confirm_summary"
