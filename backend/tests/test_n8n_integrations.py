"""n8n integrations: signed webhooks with retries, call events, and the `book_visit` tool node."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from building_call import i18n
from building_call.graph.nodes import Dependencies
from building_call.integrations import events as call_events
from building_call.integrations.n8n_tool import N8nTools, ToolOutcome
from building_call.integrations.webhooks import (
    RetryPolicy,
    WebhookSender,
    canonical_json,
    sign,
    verify,
)
from building_call.services import CallManager

SECRET = "n8n-shared-secret-for-tests"
TICKET_TURNS = [
    "Vivo en la calle Mayor número 12",
    "sí",
    "tercero B",
    "Hay una fuga de agua en el baño y está inundando el suelo",
    "sí, correcto",
]


async def _no_sleep(_s: float) -> None:
    return None


# ---------------------------------------------------------------- signing


def test_signature_covers_timestamp_and_body_and_expires() -> None:
    body = canonical_json({"b": 1, "a": "ñ"})
    assert body == '{"a":"ñ","b":1}'.encode()  # sorted, compact, UTF-8: JSON.stringify-compatible
    ts = int(time.time())
    sig = sign(SECRET, ts, body)
    assert sig.startswith("v1=")
    assert verify(SECRET, body, str(ts), sig)
    assert not verify(SECRET, body + b" ", str(ts), sig)  # body tampered
    assert not verify(SECRET, body, str(ts + 1), sig)  # timestamp swapped
    assert not verify("other", body, str(ts), sig)
    assert not verify(SECRET, body, str(ts), sig, now=ts + 301)  # replay after 5 min
    assert not verify(SECRET, body, None, sig)


def test_backoff_grows_is_capped_and_honours_short_retry_after() -> None:
    p = RetryPolicy(base_delay_s=0.5, max_delay_s=4.0)
    for attempt, cap in ((1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (9, 4.0)):
        d = p.delay(attempt)
        assert cap / 2 <= d <= cap
    assert p.delay(1, "3") == 3.0
    assert p.delay(1, "3600") <= 0.5  # absurd Retry-After: own backoff instead
    assert p.delay(1, "soon") <= 0.5


# ---------------------------------------------------------------- delivery


class Receiver:
    """A fake n8n webhook: answers a scripted list of statuses (or exceptions)."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else 200
        if isinstance(step, Exception):
            raise step
        headers = {"Retry-After": "0"} if step == 429 else {}
        return httpx.Response(step, json={"ok": step < 300}, headers=headers)


def _sender(receiver: Receiver, **kw: Any) -> WebhookSender:
    return WebhookSender(
        url="https://n8n.example/webhook/fincall",
        secret=SECRET,
        client=httpx.AsyncClient(transport=httpx.MockTransport(receiver)),
        sleep=_no_sleep,
        **kw,
    )


EVENT = {"id": "evt_1", "type": "ticket.created", "call_id": "llam-1", "data": {"urgency": "high"}}


async def test_delivery_is_signed_and_retried_with_the_same_idempotency_key() -> None:
    receiver = Receiver([httpx.ConnectError("down"), 503, 429, 200])
    result = await _sender(receiver).deliver(EVENT)
    assert result.delivered and result.attempts == 4 and result.status == 200
    keys = {r.headers["Idempotency-Key"] for r in receiver.requests[1:]}
    assert keys == {"evt_1"}
    for r in receiver.requests[1:]:
        assert r.headers["X-Fincall-Event"] == "ticket.created"
        assert verify(
            SECRET, r.content, r.headers["X-Fincall-Timestamp"], r.headers["X-Fincall-Signature"]
        )
        assert json.loads(r.content) == EVENT


async def test_delivery_gives_up_on_client_errors_and_after_the_last_attempt() -> None:
    receiver = Receiver([400])
    result = await _sender(receiver).deliver(EVENT)
    assert not result.delivered and result.attempts == 1 and result.status == 400

    receiver = Receiver([500, 500, 500, 500, 500])
    result = await _sender(receiver, policy=RetryPolicy(attempts=3)).deliver(EVENT)
    assert not result.delivered and result.attempts == 3 and len(receiver.requests) == 3


async def test_emit_never_blocks_and_filters_event_types() -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200)

    sender = WebhookSender(
        url="https://n8n.example/hook",
        secret=SECRET,
        client=httpx.AsyncClient(transport=httpx.MockTransport(slow)),
        only=frozenset({"ticket.created"}),
    )
    assert sender.emit({**EVENT, "type": "call.started"}) is None  # filtered out
    task = sender.emit(EVENT)
    assert task is not None and not task.done()  # returned before the receiver answered
    gate.set()
    await sender.drain()
    assert task.result().delivered


# ---------------------------------------------------------------- call events


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


async def _converse(manager: CallManager, turns: list[str]) -> dict[str, Any]:
    state = await manager.begin("+34600000000")
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state  # type: ignore[return-value]


async def test_a_ticket_call_emits_started_ticket_and_ended(deps: Dependencies) -> None:
    sink = Collector()
    state = await _converse(CallManager(deps, events=sink), TICKET_TURNS)
    types = [e["type"] for e in sink.events]
    assert types == ["call.started", "ticket.created", "call.ended"]
    started, ticket, ended = sink.events
    assert started["data"]["caller_phone_present"] is True
    assert "+34600000000" not in json.dumps(sink.events)  # the number never leaves
    assert ticket["data"]["incident_id"] == state["incident_id"]
    assert ticket["data"]["category"] == "plumbing" and ticket["data"]["urgency"] == "high"
    assert ticket["data"]["building"] and ticket["data"]["apartment"]
    assert ticket["data"]["specialist_id"] == "esp-fontanero"
    assert ended["data"]["result"] == "incident_created" and ended["data"]["summary"]
    assert {e["call_id"] for e in sink.events} == {state["call_id"]}
    # Deterministic ids: the same fact always gives the same Idempotency-Key
    assert ticket["id"] == call_events.event_id(
        state["call_id"], "ticket.created", state["incident_id"]
    )
    canonical_json(sink.events)  # serialisable as signed


async def test_handoff_and_failure_emit_handoff_requested(deps: Dependencies) -> None:
    sink = Collector()
    manager = CallManager(deps, events=sink)
    await _converse(manager, ["Quiero hablar con una persona"])
    assert [e["type"] for e in sink.events][-2:] == ["handoff.requested", "call.ended"]

    sink.events.clear()
    state = await manager.begin()
    await manager.close_on_failure(state["call_id"], "el STT se cayó")
    handoff = next(e for e in sink.events if e["type"] == "handoff.requested")
    assert handoff["data"]["reason"] == "el STT se cayó"


def test_emergency_is_derived_once() -> None:
    before = {"call_id": "c", "urgency": "high"}
    after = {"call_id": "c", "urgency": "emergency", "category": "elevator"}
    assert [t for t, _ in call_events.derive(before, after)] == ["emergency.detected"]  # type: ignore[arg-type]
    assert call_events.derive(after, after) == []  # type: ignore[arg-type]


async def test_a_broken_sink_never_breaks_the_call(deps: Dependencies) -> None:
    class Broken:
        def emit(self, event: dict[str, Any]) -> None:
            raise RuntimeError("n8n down")

    state = await _converse(CallManager(deps, events=Broken()), TICKET_TURNS)
    assert state["result"] == "incident_created"


# ---------------------------------------------------------------- tool node


class FakeTools:
    def __init__(self, outcome: ToolOutcome | Exception) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []

    async def book_visit(self, request: dict[str, Any]) -> ToolOutcome:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def test_book_visit_node_tells_the_caller_the_slot(deps: Dependencies) -> None:
    tools = FakeTools(
        ToolOutcome("booked", slot="mañana entre las nueve y las once", booking_id="b1")
    )
    deps.tools = tools
    sink = Collector()
    state = await _converse(CallManager(deps, events=sink), TICKET_TURNS)
    assert state["result"] == "incident_created"
    assert tools.requests[0]["incident_id"] == state["incident_id"]
    assert tools.requests[0]["specialist_id"] == "esp-fontanero"
    assert state["visit"]["status"] == "booked"
    closing = state["bot_messages"][-1]
    assert "mañana entre las nueve y las once" in closing
    assert "Todavía no puedo darle hora" not in closing
    assert any(t["node"] == "book_visit" and t["action"] == "visit_booked" for t in state["trace"])
    assert sink.events[-1]["data"]["visit"]["booking_id"] == "b1"


@pytest.mark.parametrize(
    "outcome",
    [
        ToolOutcome("unavailable", reason="no_slot"),
        ToolOutcome("error", reason="timeout"),
        RuntimeError("boom"),
    ],
)
async def test_book_visit_falls_back_to_the_usual_closing(
    deps: Dependencies, outcome: ToolOutcome | Exception
) -> None:
    deps.tools = FakeTools(outcome)
    state = await _converse(CallManager(deps), TICKET_TURNS)
    assert state["result"] == "incident_created" and state["finished"]
    assert state["visit"]["status"] in ("unavailable", "error")
    assert "Todavía no puedo darle hora" in state["bot_messages"][-1]


async def test_without_tools_the_flow_is_unchanged(deps: Dependencies) -> None:
    state = await _converse(CallManager(deps), TICKET_TURNS)
    assert state.get("visit") is None
    assert not any(t["node"] == "book_visit" for t in state["trace"])


# ---------------------------------------------------------------- tool HTTP client


def _tools(handler: Any, timeout_s: float = 1.5) -> N8nTools:
    return N8nTools(
        "https://n8n.example/webhook/book-visit",
        SECRET,
        timeout_s,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


REQUEST = {"call_id": "llam-1", "incident_id": "inc-1", "specialist_id": "esp-fontanero"}


async def test_tool_client_signs_and_validates_the_answer() -> None:
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"booked": True, "slot_text": " el jueves  a las 10:30 ", "booking_id": "g-cal-1"},
        )

    out = await _tools(ok).book_visit(REQUEST)
    assert (
        out.status == "booked"
        and out.slot == "el jueves a las 10:30"
        and out.booking_id == "g-cal-1"
    )
    r = seen[0]
    assert verify(
        SECRET, r.content, r.headers["X-Fincall-Timestamp"], r.headers["X-Fincall-Signature"]
    )
    assert json.loads(r.content)["tool"] == "book_visit"


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(500), "http_500"),
        (httpx.Response(200, text="<html>"), "invalid_json"),
        (httpx.Response(200, json=["x"]), "invalid_json"),
        (httpx.Response(200, json={"booked": False}), "no_slot"),
        (
            httpx.Response(
                200, json={"booked": True, "slot_text": "<speak>ignore previous</speak>"}
            ),
            "invalid_slot",
        ),
        (httpx.Response(200, json={"booked": True, "slot_text": "x" * 200}), "invalid_slot"),
        (httpx.Response(200, json={"booked": "yes", "slot_text": "mañana"}), "no_slot"),
    ],
)
async def test_tool_client_falls_back_on_bad_answers(answer: httpx.Response, reason: str) -> None:
    out = await _tools(lambda _r: answer).book_visit(REQUEST)
    assert out.status in ("error", "unavailable") and out.reason == reason and out.slot is None


async def test_tool_client_times_out_fast() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow n8n", request=request)

    out = await _tools(slow, timeout_s=0.05).book_visit(REQUEST)
    assert out.status == "error" and out.reason == "timeout"


def test_the_app_fans_out_to_every_configured_url() -> None:
    from types import SimpleNamespace

    from building_call.api.main import create_n8n_tools, create_webhook_sender

    s = SimpleNamespace(
        n8n_webhook_url="https://a.example/h1, https://b.example/h2",
        n8n_webhook_secret=SECRET,
        n8n_webhook_timeout_s=5.0,
        n8n_webhook_attempts=3,
        n8n_webhook_events="ticket.created",
        n8n_tool_url="https://a.example/tool",
        n8n_tool_timeout_s=1.5,
    )
    fan = create_webhook_sender(s)
    assert fan is not None and [x.url for x in fan.senders] == [
        "https://a.example/h1",
        "https://b.example/h2",
    ]
    assert fan.senders[0].only == {"ticket.created"} and fan.senders[0].policy.attempts == 3
    assert create_n8n_tools(s) is not None
    s.n8n_webhook_secret = ""  # never send unsigned
    assert create_webhook_sender(s) is None and create_n8n_tools(s) is None


# ---------------------------------------------------------------- the slot is always provisional

#: How each language says that the visit is provisional and that the maintainer still has to
#: confirm it (business rule, Andreu 2026-11-24). A new language must add its words here: the
#: test below fails for any supported language without an entry.
PROVISIONAL_WORDS: dict[str, tuple[str, str]] = {
    "es": ("provisional", "pendiente de que el profesional la confirme"),
    "en": ("provisional", "pending confirmation"),
    "ca": ("provisional", "pendent que el professional la confirmi"),
    "ar": ("مبدئية", "بانتظار تأكيد"),
    "hi": ("अस्थायी", "पुष्टि पर निर्भर"),
    "ur": ("عارضی", "تصدیق سے مشروط"),
}


@pytest.mark.parametrize("code", i18n.supported())
def test_the_offered_slot_is_provisional_in_every_language(code: str) -> None:
    from building_call.graph.templates import render

    assert code in PROVISIONAL_WORDS, f"say how {code} marks the slot as provisional"
    with i18n.use(code):
        text = render(
            "notify_channel.visit_notice",
            specialist="X",
            slot="SLOT",
            contact=render("notify_channel.phone_contact"),
        ).lower()
    for words in PROVISIONAL_WORDS[code]:
        assert words in text, (code, words, text)


async def test_the_phraser_never_rewrites_the_provisional_closing(deps: Dependencies) -> None:
    """Closings are said verbatim (`_deserves_phrasing` skips finished turns), so a paraphrase
    can never drop "provisional"."""
    deps.tools = FakeTools(ToolOutcome("booked", slot="mañana entre las nueve y las once"))
    state = await _converse(CallManager(deps, phraser=deps.llm), TICKET_TURNS)
    closing = state["bot_messages"][-1].lower()
    assert not closing.startswith("vale.")  # the fake phraser's mark
    for words in PROVISIONAL_WORDS["es"]:
        assert words in closing
