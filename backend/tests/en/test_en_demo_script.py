"""The exact call of the English demo video, end to end with the fake model.

12 Main Street -> "2A" -> "no, it's 3A" (the apartment is read back and corrected) -> the
elevator is completely stopped, nobody inside -> the caller talks over the summary (only 20 %
of it played) -> the summary is said again -> "yes" -> incident for the elevator's maintainer.
"""

from __future__ import annotations

from building_call.services import CallManager


async def test_the_demo_call_in_english(detail_manager: CallManager) -> None:
    manager = detail_manager
    state = await manager.begin(language="en")
    assert state["bot_messages"][0].startswith("Hi, you've reached FinCall")

    state = await manager.message(state["call_id"], "Hi, I live at 12 Main Street")
    assert state["bot_messages"][-1] == "I have 12 Main Street. Is that right?"
    state = await manager.message(state["call_id"], "Yes")
    assert state["current_node"] == "identify_home"

    state = await manager.message(state["call_id"], "2A")
    assert state["home_id"] == "estate-main-12-u-2A"
    assert state["bot_messages"][-1] == "So that's 12 Main Street, apartment 2A. Is that right?"

    # The caller corrects the apartment: it is looked up again and read back
    state = await manager.message(state["call_id"], "No, it's 3A")
    assert state["home_id"] == "estate-main-12-u-3A"
    assert state["bot_messages"][-1] == "So that's 12 Main Street, apartment 3A. Is that right?"
    state = await manager.message(state["call_id"], "Yes, that's right")
    assert state["home_confirmed"] is True
    assert state["current_node"] == "collect_request"

    state = await manager.message(state["call_id"], "The elevator is completely stopped")
    assert state["category"] == "elevator"
    assert state["equipment_id"] == "eq-main12-elevator"
    # One detail question, the elevator's own (stopped or on and off, anyone inside?)
    assert state["current_node"] == "ask_detail"
    assert "anyone inside" in state["bot_messages"][-1]

    state = await manager.message(state["call_id"], "Completely stopped, nobody's inside")
    assert state["current_node"] == "confirm_summary"
    summary = state["bot_messages"][-1]
    assert summary.startswith("Let me check I've got this right:")

    # The caller talks over the summary: only its start was heard, so a "yes" doesn't count
    await manager.record_delivery(
        state["call_id"], state["turn"], "interrupted", 0.2, summary[: len(summary) // 5]
    )
    state = await manager.message(state["call_id"], "Yes")
    assert state["current_node"] == "confirm_summary"
    assert state["confirmed"] is not True
    assert summary in state["bot_messages"][-1]  # said again, in full

    await manager.record_delivery(state["call_id"], state["turn"], "completed", 1.0, None)
    state = await manager.message(state["call_id"], "Yes")
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert state["language"] == "en"
    assert state["specialist_id"] == "spc-elevators"
    closing = state["bot_messages"][-1]
    assert "North Elevators" in closing and "twenty-four hours" in closing
    assert all(t["text"] for t in state["history"])
