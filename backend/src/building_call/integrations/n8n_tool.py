"""Tools the agent calls *during* a call through an n8n webhook ("Respond to Webhook").

The first tool is `book_visit`: once the ticket exists and has a maintainer, ask n8n to find a
slot in that maintainer's calendar and hold it. n8n answers synchronously:

    {"booked": true, "slot_text": "mañana entre las nueve y las once", "booking_id": "abc"}

The answer is untrusted input that will be *spoken* to the caller, so it is validated in code:
a short single line of plain text, or it is ignored. Any failure (timeout, network, non-2xx,
bad JSON, `booked` not true) returns a fallback and the call closes exactly as it would without
the tool. The request is signed like the outgoing webhooks (`webhooks.signed_headers`).

Latency: this runs inside the caller's last turn, so the timeout is short (1.5 s by default) and
counts towards `response_ms`. Measure before raising it.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from building_call.integrations.events import event_id
from building_call.integrations.webhooks import canonical_json, signed_headers

logger = logging.getLogger(__name__)

MAX_SLOT_CHARS = 80
#: Unicode categories a spoken time may use in any language and script: letters, combining
#: marks (Devanagari vowel signs, Arabic harakat), digits, spaces and plain punctuation
_SLOT_CATEGORIES = ("L", "M", "N", "Zs")
_SLOT_PUNCTUATION = frozenset(",.:/-'’()،؛")


@dataclass(frozen=True)
class ToolOutcome:
    status: str  # "booked" | "unavailable" | "error"
    slot: str | None = None
    booking_id: str | None = None
    reason: str | None = None
    ms: int = 0

    def as_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "slot": self.slot,
            "booking_id": self.booking_id,
            "reason": self.reason,
            "ms": self.ms,
        }


def _clean_slot(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not 3 <= len(text) <= MAX_SLOT_CHARS:
        return None
    # No markup, quotes, braces, control or format characters: it is spoken, not rendered
    if not all(
        c in _SLOT_PUNCTUATION or unicodedata.category(c).startswith(_SLOT_CATEGORIES) for c in text
    ):
        return None
    return text


def _clean_id(value: Any) -> str | None:
    if isinstance(value, str | int) and re.fullmatch(r"[\w\-.:@]{1,128}", str(value)):
        return str(value)
    return None


class N8nTools:
    def __init__(
        self,
        url: str,
        secret: str,
        timeout_s: float = 1.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.secret = secret
        self.timeout_s = timeout_s
        self._client = client

    async def book_visit(self, request: dict[str, Any]) -> ToolOutcome:
        t0 = perf_counter()

        def outcome(status: str, **kw: Any) -> ToolOutcome:
            return ToolOutcome(status, ms=int((perf_counter() - t0) * 1000), **kw)

        payload = {"tool": "book_visit", **request}
        body = canonical_json(payload)
        key = event_id(
            str(request.get("call_id", "")), "tool.book_visit", str(request.get("incident_id", ""))
        )
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            r = await client.post(
                self.url,
                content=body,
                headers=signed_headers(self.secret, "tool.book_visit", key, body),
                timeout=self.timeout_s,
            )
            if not r.is_success:
                return outcome("error", reason=f"http_{r.status_code}")
            data = r.json()
        except httpx.TimeoutException:
            return outcome("error", reason="timeout")
        except httpx.HTTPError as e:
            return outcome("error", reason=type(e).__name__)
        except ValueError:
            return outcome("error", reason="invalid_json")
        finally:
            if self._client is None:
                await client.aclose()
        if not isinstance(data, dict):
            return outcome("error", reason="invalid_json")
        if data.get("booked") is not True:
            return outcome("unavailable", reason="no_slot")
        slot = _clean_slot(data.get("slot_text"))
        if slot is None:
            return outcome("error", reason="invalid_slot")
        return outcome("booked", slot=slot, booking_id=_clean_id(data.get("booking_id")))
