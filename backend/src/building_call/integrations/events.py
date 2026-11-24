"""Call events for outside automations, derived from how the call state changed in a turn.

The graph does not know about webhooks: after each turn `CallManager` compares the state before
and after and publishes what happened. That keeps the nodes free of side channels and makes every
path (console, Twilio, Vonage, SIP through LiveKit) emit the same events.

| type                | when                                                       |
|---------------------|------------------------------------------------------------|
| `call.started`      | the call is created (greeting turn)                        |
| `emergency.detected`| urgency becomes `emergency`                                |
| `ticket.created`    | the incident is saved                                      |
| `handoff.requested` | the call is handed to the administrator (caller asked for a person, or the assistant failed) |
| `call.ended`        | the call finishes, with its summary                        |

Privacy: the caller's phone number is never sent (only whether there is one). The event id is
deterministic, so the same fact always produces the same `Idempotency-Key`.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from building_call.graph.state import CallState

logger = logging.getLogger(__name__)

EVENT_TYPES = (
    "call.started",
    "emergency.detected",
    "ticket.created",
    "handoff.requested",
    "call.ended",
)


class EventSink(Protocol):
    def emit(self, event: dict[str, Any]) -> Any: ...


def event_id(call_id: str, event_type: str, discriminator: str = "") -> str:
    digest = hashlib.sha256(f"{call_id}|{event_type}|{discriminator}".encode()).hexdigest()
    return f"evt_{digest[:24]}"


def _where(state: CallState) -> dict[str, Any]:
    return {
        "estate_id": state.get("estate_id"),
        "building": state.get("estate_address"),
        "home_id": state.get("home_id"),
        "apartment": state.get("home_label"),
    }


def _duration_s(state: CallState, now: datetime) -> int | None:
    try:
        return int((now - datetime.fromisoformat(state["started_at"])).total_seconds())
    except (KeyError, ValueError):
        return None


def derive(previous: CallState | None, fresh: CallState) -> list[tuple[str, dict[str, Any]]]:
    """(type, data) of every event this transition produced, in the order they happened."""
    before: CallState = previous or {}
    out: list[tuple[str, dict[str, Any]]] = []
    if previous is None:
        out.append(
            (
                "call.started",
                {
                    "started_at": fresh.get("started_at"),
                    "graph": fresh.get("graph"),
                    "caller_phone_present": bool(fresh.get("caller_phone")),
                },
            )
        )
    if fresh.get("urgency") == "emergency" and before.get("urgency") != "emergency":
        out.append(
            (
                "emergency.detected",
                {
                    **_where(fresh),
                    "category": fresh.get("category"),
                    "request": fresh.get("request"),
                },
            )
        )
    if fresh.get("incident_id") and not before.get("incident_id"):
        out.append(
            (
                "ticket.created",
                {
                    "incident_id": fresh.get("incident_id"),
                    "category": fresh.get("category"),
                    "urgency": fresh.get("urgency"),
                    "work_type": fresh.get("work_type") or "repair",
                    **_where(fresh),
                    "summary": fresh.get("summary") or fresh.get("request"),
                    "specialist_id": fresh.get("specialist_id"),
                    "specialist_name": fresh.get("specialist_name"),
                    "needs_administrator": fresh.get("specialist_id") is None,
                    "review_reason": fresh.get("review_reason"),
                },
            )
        )
    if (
        fresh.get("result") == "handed_to_administrator"
        and before.get("result") != "handed_to_administrator"
    ):
        out.append(
            (
                "handoff.requested",
                {
                    **_where(fresh),
                    "reason": fresh.get("review_reason") or "caller_asked_for_a_person",
                    "summary": fresh.get("summary") or fresh.get("request"),
                },
            )
        )
    if fresh.get("finished") and not before.get("finished"):
        now = datetime.now(UTC)
        out.append(
            (
                "call.ended",
                {
                    "result": fresh.get("result"),
                    "duration_s": _duration_s(fresh, now),
                    "turns": fresh.get("turn", 0),
                    "incident_id": fresh.get("incident_id"),
                    "category": fresh.get("category"),
                    "urgency": fresh.get("urgency"),
                    **_where(fresh),
                    "summary": fresh.get("summary") or fresh.get("request"),
                    "needs_review": fresh.get("result") != "incident_created",
                    "visit": fresh.get("visit"),
                },
            )
        )
    return out


def envelope(call_id: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    discriminator = str(data.get("incident_id") or "")
    return {
        "id": event_id(call_id, event_type, discriminator),
        "type": event_type,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "call_id": call_id,
        "data": data,
    }


def publish(sink: EventSink | None, previous: CallState | None, fresh: CallState) -> int:
    """Emit the events of a transition. Never raises: automations are best-effort."""
    if sink is None:
        return 0
    count = 0
    for event_type, data in derive(previous, fresh):
        try:
            sink.emit(envelope(fresh["call_id"], event_type, data))
            count += 1
        except Exception:  # noqa: BLE001 - a broken sink never breaks a call
            logger.exception("No se pudo emitir %s", event_type)
    return count
