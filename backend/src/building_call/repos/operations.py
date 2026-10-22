"""Consultas y agregados de la consola operativa."""

from __future__ import annotations

from statistics import median
from typing import Any

RESOLVED_RESULTS = {"incident_created"}


def _summary(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    sorted_values = sorted(values)
    p95_index = round(0.95 * (len(sorted_values) - 1))
    return {
        "n": len(sorted_values),
        "p50": round(median(sorted_values)),
        "p95": sorted_values[p95_index],
        "max": sorted_values[-1],
    }


def summarize_operations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [
        int(row["response_ms"])
        for row in rows
        if isinstance(row.get("response_ms"), int | float)
    ]
    resolved = sum(row.get("result") in RESOLVED_RESULTS for row in rows)
    return {
        "calls": len(rows),
        "response_ms": _summary(latencies),
        "resolved": resolved,
        "unresolved": len(rows) - resolved,
    }
