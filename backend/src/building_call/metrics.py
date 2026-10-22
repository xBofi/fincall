"""Métricas de latencia del pipeline de voz.

La métrica principal es `response_ms`: desde el final del audio del vecino hasta el inicio del
audio del bot. Umbrales fijados por Andreu (2026-08-28):

    <= 700 ms  excelente
    <= 1200 ms objetivo
    <= 1500 ms tolerable
    <= 2000 ms mal
    >  2000 ms muy mal

El servidor mide sus etapas (STT, interpretación LLM, grafo); el navegador aporta las suyas
(VAD/EOU, TTS hasta el primer audio) y el total, porque solo él ve los instantes de audio real.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

THRESHOLDS_MS: tuple[tuple[int, str], ...] = (
    (700, "excellent"),
    (1200, "target"),
    (1500, "tolerable"),
    (2000, "bad"),
)


def rate(response_ms: float | None) -> str | None:
    if response_ms is None:
        return None
    for limit, name in THRESHOLDS_MS:
        if response_ms <= limit:
            return name
    return "very_bad"


@dataclass
class TurnTimings:
    """Tiempos de un turno. Los del servidor los rellena el gestor; los del cliente llegan por API."""

    call_id: str
    turn: int
    initial_node: str
    final_node: str
    # Servidor
    stt_ms: int | None = None
    interpretation_ms: int | None = None
    classification_ms: int | None = None
    llm_calls: int = 0
    graph_ms: int | None = None
    server_ms: int | None = None
    interpretation_origin: str | None = None  # determinista | llm | ninguna
    # Cliente
    vad_silence_ms: int | None = None
    network_ms: int | None = None
    tts_start_ms: int | None = None
    response_ms: int | None = None
    stt_client: str | None = None  # navegador | servidor
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def rating(self) -> str | None:
        return rate(self.response_ms)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rating": self.rating}


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    order = sorted(values)
    k = max(0, min(len(order) - 1, round(q * (len(order) - 1))))
    return order[k]


def summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "p50": round(median(values)) if values else None,
        "p95": _p(values, 0.95),
        "max": max(values) if values else None,
    }


class MetricsRegistry:
    """Guarda los últimos N turnos en memoria y calcula agregados."""

    def __init__(self, capacity: int = 2000) -> None:
        self._turns: deque[TurnTimings] = deque(maxlen=capacity)
        self._index: dict[tuple[str, int], TurnTimings] = {}

    def record(self, timings: TurnTimings) -> None:
        if len(self._turns) == self._turns.maxlen:
            oldest = self._turns[0]  # el deque lo va a expulsar: el índice tampoco lo guarda
            self._index.pop((oldest.call_id, oldest.turn), None)
        self._turns.append(timings)
        self._index[(timings.call_id, timings.turn)] = timings

    def complete_client(
        self, call_id: str, turn: int, data: dict[str, Any]
    ) -> TurnTimings | None:
        t = self._index.get((call_id, turn))
        if t is None:
            return None
        for key in ("vad_silence_ms", "network_ms", "tts_start_ms", "response_ms"):
            if isinstance(data.get(key), int | float):
                setattr(t, key, int(data[key]))
        if isinstance(data.get("stt_client"), str):
            t.stt_client = data["stt_client"]
        if isinstance(data.get("extra"), dict):
            t.extra.update(data["extra"])
        return t

    def turns(self, call_id: str | None = None) -> list[TurnTimings]:
        return [t for t in self._turns if call_id is None or t.call_id == call_id]

    def aggregates(self, call_id: str | None = None) -> dict[str, Any]:
        ts = self.turns(call_id)

        def col(name: str) -> list[float]:
            return [float(getattr(t, name)) for t in ts if getattr(t, name) is not None]

        reply = col("response_ms")
        with_llm = [t for t in ts if t.llm_calls > 0]
        without_llm = [t for t in ts if t.llm_calls == 0]
        return {
            "turns": len(ts),
            "response_ms": summary(reply),
            "ratings": {
                name: sum(1 for v in reply if rate(v) == name)
                for name in (*[n for _, n in THRESHOLDS_MS], "very_bad")
            },
            "server_ms": summary(col("server_ms")),
            "server_ms_with_llm": summary(
                [float(t.server_ms) for t in with_llm if t.server_ms is not None]
            ),
            "server_ms_without_llm": summary(
                [float(t.server_ms) for t in without_llm if t.server_ms is not None]
            ),
            "stt_ms": summary(col("stt_ms")),
            "interpretation_ms": summary(col("interpretation_ms")),
            "classification_ms": summary(col("classification_ms")),
            "vad_silence_ms": summary(col("vad_silence_ms")),
            "tts_start_ms": summary(col("tts_start_ms")),
            "thresholds_ms": {name: limit for limit, name in THRESHOLDS_MS},
        }
