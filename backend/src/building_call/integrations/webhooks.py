"""Signed outgoing webhooks (n8n, Zapier, Make or any HTTP endpoint).

Every request carries:

- `X-Fincall-Event`: the event type (`ticket.created`, ...).
- `X-Fincall-Timestamp`: Unix seconds when the request was signed.
- `X-Fincall-Signature`: `v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>`. The
  timestamp is inside the signed string, so a captured request cannot be replayed later with a
  fresh timestamp; receivers reject anything older than a few minutes.
- `Idempotency-Key`: the event id, stable across retries. A receiver that already processed it
  answers 2xx and does nothing (n8n: a "Remove Duplicates" or a data-table lookup on the key).

The body is canonical JSON (sorted keys, no spaces, UTF-8, no floats), so a receiver that only
has the parsed object (an n8n Code node) can rebuild the exact signed bytes with
`JSON.stringify`.

Delivery is best-effort and never blocks a call: `emit()` returns at once and the request runs in
a background task with a timeout, retries with exponential backoff and jitter on network errors,
429 and 5xx (honouring a short `Retry-After`), and gives up on other 4xx. Failures are logged,
never raised into the call.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SIGNATURE_VERSION = "v1"
DEFAULT_TOLERANCE_S = 300
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


def verify(
    secret: str,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
    now: float | None = None,
) -> bool:
    """Receiver side, for tests and for anyone receiving our webhooks in Python."""
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - ts) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, ts, body), signature)


def signed_headers(secret: str, event_type: str, event_id: str, body: bytes) -> dict[str, str]:
    ts = int(time.time())
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "fincall-webhooks/1",
        "X-Fincall-Event": event_type,
        "X-Fincall-Timestamp": str(ts),
        "X-Fincall-Signature": sign(secret, ts, body),
        "Idempotency-Key": event_id,
    }


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    #: Longest `Retry-After` we accept; beyond it we use our own backoff
    max_retry_after_s: float = 30.0

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Delay before retry number `attempt` (1-based), full jitter on top of the backoff."""
        if retry_after:
            try:
                seconds = float(retry_after)
                if 0 <= seconds <= self.max_retry_after_s:
                    return seconds
            except ValueError:
                pass
        backoff = float(min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1)))
        return backoff / 2 + random.uniform(0, backoff / 2)  # noqa: S311 - jitter, not crypto


@dataclass
class DeliveryResult:
    event_id: str
    delivered: bool
    attempts: int
    status: int | None = None
    error: str | None = None


Sleep = Callable[[float], Awaitable[None]]


@dataclass
class WebhookSender:
    url: str
    secret: str
    timeout_s: float = 5.0
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    client: httpx.AsyncClient | None = None
    sleep: Sleep = asyncio.sleep
    #: Event types to send; empty = all
    only: frozenset[str] = frozenset()
    max_in_flight: int = 16
    _tasks: set[asyncio.Task[DeliveryResult]] = field(default_factory=set, init=False)
    _semaphore: asyncio.Semaphore | None = field(default=None, init=False)

    def wants(self, event_type: str) -> bool:
        return not self.only or event_type in self.only

    async def deliver(self, event: dict[str, Any]) -> DeliveryResult:
        """Send one event, retrying as the policy says. Never raises."""
        body = canonical_json(event)
        event_id, event_type = str(event["id"]), str(event["type"])
        client = self.client or httpx.AsyncClient(timeout=self.timeout_s)
        last = DeliveryResult(event_id, False, 0)
        try:
            for attempt in range(1, self.policy.attempts + 1):
                # Signed per attempt: the timestamp must be fresh when the receiver checks it
                headers = signed_headers(self.secret, event_type, event_id, body)
                retry_after: str | None = None
                try:
                    r = await client.post(
                        self.url, content=body, headers=headers, timeout=self.timeout_s
                    )
                    last = DeliveryResult(event_id, r.is_success, attempt, r.status_code)
                    if r.is_success:
                        return last
                    if r.status_code not in RETRY_STATUSES:
                        logger.warning(
                            "Webhook %s (%s) rechazado con %s: no se reintenta",
                            event_type,
                            event_id,
                            r.status_code,
                        )
                        return last
                    retry_after = r.headers.get("Retry-After")
                except httpx.HTTPError as e:
                    last = DeliveryResult(event_id, False, attempt, None, type(e).__name__)
                if attempt < self.policy.attempts:
                    await self.sleep(self.policy.delay(attempt, retry_after))
            logger.warning(
                "Webhook %s (%s) sin entregar tras %d intentos (%s)",
                event_type,
                event_id,
                last.attempts,
                last.status or last.error,
            )
            return last
        finally:
            if self.client is None:
                await client.aclose()

    def emit(self, event: dict[str, Any]) -> asyncio.Task[DeliveryResult] | None:
        """Fire and forget: schedules the delivery and returns immediately."""
        if not self.wants(str(event["type"])):
            return None
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_in_flight)
        semaphore = self._semaphore

        async def run() -> DeliveryResult:
            async with semaphore:
                return await self.deliver(event)

        task = asyncio.create_task(run(), name=f"webhook-{event['type']}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait (bounded) for deliveries in flight, e.g. on shutdown."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout_s)


@dataclass
class FanOut:
    """Several receivers (e.g. one n8n workflow per use case): each gets every event it wants,
    with its own retries. One slow or broken receiver does not delay the others."""

    senders: list[WebhookSender]

    def emit(self, event: dict[str, Any]) -> list[asyncio.Task[DeliveryResult]]:
        return [t for s in self.senders if (t := s.emit(event)) is not None]

    async def drain(self, timeout_s: float = 5.0) -> None:
        await asyncio.gather(*(s.drain(timeout_s) for s in self.senders))
