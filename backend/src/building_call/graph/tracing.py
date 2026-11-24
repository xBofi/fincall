"""Traza de ejecución del grafo: qué nodo se visitó, cuánto tardó, a dónde saltó y por qué.

Cada nodo registrado en `registry.py` pasa por `instrument()`. El envoltorio no cambia lo que
el nodo hace: mide el tiempo, compara el estado de entrada con lo que el nodo devuelve y añade
una visita a `turn_visits`. La misma visita se publica en el bus en vivo (`TRACE_BUS`) para la
vista de enrutado de la consola (SSE en `/api/calls/{id}/events`).

La razón de cada salto es la condición que el manifiesto declara para esa arista, así que la
vista nunca inventa por qué se fue a un sitio: si la arista no estuviera declarada, `declared`
sería falso (y `nodes._verify` ya la habría anotado como violación).

Privacidad: el diff de estado excluye el teléfono, el historial completo y la configuración;
las visitas se guardan con el turno, así que el borrado de la llamada también las borra.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import perf_counter
from typing import Any

from building_call.graph.manifest import MANIFEST

logger = logging.getLogger(__name__)

#: Claves que no entran en el diff: datos personales, listas que ya se ven en otro sitio
#: (conversación, traza) o ruido interno del turno.
_DIFF_EXCLUDED = frozenset(
    {
        "caller_phone",
        "history",
        "trace",
        "bot_messages",
        "bot_pieces",
        "turn_notices",
        "turn_timings",
        "turn_visits",
        "user_utterance",
        "equipment_list",
        "config",
        "last_question",
        "last_bot_message",
        "last_delivery",
        "validations",
        "interpretation",
        "awaiting_user",
        "current_node",
    }
)

_MAX_TEXT = 140

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _short(value: Any) -> Any:
    """Valor apto para la consola: textos recortados, colecciones pequeñas tal cual."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[: _MAX_TEXT - 1] + "…"
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in list(value.items())[:12]}
    if isinstance(value, list | tuple):
        return [_short(v) for v in list(value)[:12]]
    return value


def state_diff(before: dict[str, Any], update: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Las claves de negocio que el nodo cambió, con su valor antes y después."""
    diff: dict[str, dict[str, Any]] = {}
    for key, after in update.items():
        if key in _DIFF_EXCLUDED:
            continue
        prior = before.get(key)
        if prior == after:
            continue
        diff[key] = {"before": _short(prior), "after": _short(after)}
    return diff


def condition_for(node: str, target: str, consumed_utterance: bool) -> tuple[str | None, bool]:
    """Condición declarada para la arista `node -> target` y si lo está.

    Quedarse en el mismo nodo esperando al vecino justo tras llegar a él no es una arista: es
    la pregunta. Solo cuenta como la auto-arista declarada ("repregunta") si el nodo ya tenía
    una respuesta del vecino delante.
    """
    manifest_node = MANIFEST.get(node)
    if manifest_node is None:
        return None, False
    if target == node and not consumed_utterance:
        return None, True
    for edge in manifest_node.targets:
        if edge.target == target:
            return edge.condition, True
    return None, False


class TraceBus:
    """Publica eventos de traza por llamada a quien esté mirando. Nunca bloquea el grafo.

    Es en memoria y por proceso: con varios procesos de API, cada consola ve las llamadas del
    proceso al que está conectada (la repetición desde la base de datos no depende de esto).
    """

    QUEUE_SIZE = 256

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, call_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.QUEUE_SIZE)
        self._subscribers.setdefault(call_id, set()).add(queue)
        return queue

    def unsubscribe(self, call_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(call_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(call_id, None)

    def subscribers(self, call_id: str) -> int:
        return len(self._subscribers.get(call_id, ()))

    def publish(self, call_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(call_id, ())):
            # Una consola lenta pierde eventos; la llamada del vecino no espera por ella
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)


TRACE_BUS = TraceBus()


def _now_ms() -> int:
    return int(time.time() * 1000)


def instrument(name: str, fn: NodeFn, bus: TraceBus = TRACE_BUS) -> NodeFn:
    """Envuelve un nodo para registrar su visita. No altera lo que el nodo decide."""

    async def run(state: dict[str, Any]) -> dict[str, Any]:
        call_id = state.get("call_id", "")
        turn = state.get("turn", 0)
        index = len(state.get("turn_visits", []))
        started_at = _now_ms()
        bus.publish(
            call_id,
            {"type": "node_enter", "turn": turn, "index": index, "node": name, "at_ms": started_at},
        )
        t0 = perf_counter()
        update = await fn(state)
        ms = round((perf_counter() - t0) * 1000, 1)
        try:
            visit = _visit(name, state, update, index=index, started_at=started_at, ms=ms)
        except Exception:  # noqa: BLE001 - la traza nunca puede romper una llamada
            logger.exception("No se pudo registrar la visita a %s", name)
            return update
        update = {**update, "turn_visits": [*state.get("turn_visits", []), visit]}
        bus.publish(call_id, {"type": "node_exit", **visit})
        return update

    run.__name__ = name
    return run


def _visit(
    name: str,
    state: dict[str, Any],
    update: dict[str, Any],
    *,
    index: int,
    started_at: int,
    ms: float,
) -> dict[str, Any]:
    target = update.get("current_node", state.get("current_node", name))
    consumed = state.get("user_utterance") is not None and update.get("user_utterance", "") is None
    condition, declared = condition_for(name, target, consumed)
    timings_before = state.get("turn_timings", {}) or {}
    timings_after = update.get("turn_timings", timings_before) or {}
    llm_calls = int(timings_after.get("llm_calls", 0)) - int(timings_before.get("llm_calls", 0))
    llm_ms = 0
    for key in ("interpretation_ms", "classification_ms"):
        after, before = timings_after.get(key), timings_before.get(key)
        if after is not None and after != before:
            llm_ms += int(after)
    interpretation = update.get("interpretation")
    if interpretation == state.get("interpretation"):
        interpretation = None
    prior_trace = len(state.get("trace", []))
    trace = [
        {"action": t["action"], "detail": t["detail"]}
        for t in update.get("trace", [])[prior_trace:]
        if t.get("node") == name or t.get("node") is None
    ]
    prior_validations = len(state.get("validations", []))
    validations = list(update.get("validations", [])[prior_validations:])
    prior_messages = len(state.get("bot_messages", []))
    said = list(update.get("bot_messages", [])[prior_messages:])
    finished = bool(update.get("finished", state.get("finished", False)))
    return {
        "index": index,
        "turn": state.get("turn", 0),
        "node": name,
        "at_ms": started_at,
        "ms": ms,
        "target": target,
        "awaiting_user": bool(update.get("awaiting_user", False)),
        "finished": finished,
        "consumed_utterance": consumed,
        "condition": condition,
        "declared": declared,
        "llm_calls": max(0, llm_calls),
        "llm_ms": llm_ms,
        "interpretation": _short(interpretation) if interpretation else None,
        "confidence_signals": _short(update["confidence_signals"])
        if update.get("confidence_signals")
        and update.get("confidence_signals") != state.get("confidence_signals")
        else None,
        "trace": trace,
        "validations": validations,
        "said": said,
        "diff": state_diff(state, update),
    }
