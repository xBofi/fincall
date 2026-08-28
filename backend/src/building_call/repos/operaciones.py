"""Consultas y agregados de la consola operativa."""

from __future__ import annotations

from statistics import median
from typing import Any

RESULTADOS_RESUELTOS = {"incidencia_creada"}


def _resumen(valores: list[int]) -> dict[str, int | None]:
    if not valores:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    ordenados = sorted(valores)
    p95_indice = round(0.95 * (len(ordenados) - 1))
    return {
        "n": len(ordenados),
        "p50": round(median(ordenados)),
        "p95": ordenados[p95_indice],
        "max": ordenados[-1],
    }


def resumir_operaciones(filas: list[dict[str, Any]]) -> dict[str, Any]:
    latencias = [
        int(fila["respuesta_ms"])
        for fila in filas
        if isinstance(fila.get("respuesta_ms"), int | float)
    ]
    resueltas = sum(fila.get("resultado") in RESULTADOS_RESUELTOS for fila in filas)
    return {
        "llamadas": len(filas),
        "respuesta_ms": _resumen(latencias),
        "resueltas": resueltas,
        "no_resueltas": len(filas) - resueltas,
    }
