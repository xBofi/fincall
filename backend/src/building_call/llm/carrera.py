"""Carrera de modelos: lanza la misma petición a varios proveedores y devuelve la primera válida.

Uso previsto: Workers AI (qwen3.8-27b, 100 % exactitud, p50 ~590 ms pero p95 ~1 200 ms por red)
contra Ollama local (qwen3.5:4b, 96 %, p50 ~610 ms, p95 ~870 ms). La carrera recorta la p95 a
costa de duplicar el gasto de tokens (Workers AI es barato; el local es gratis).
"""

from __future__ import annotations

import asyncio
from typing import Any

from building_call.llm.base import ModeloLenguaje
from building_call.llm.ollama import LLMError


class ModeloCarrera:
    def __init__(self, *modelos: ModeloLenguaje) -> None:
        if not modelos:
            raise ValueError("La carrera necesita al menos un modelo")
        self.modelos = list(modelos)
        self.nombre = "carrera(" + "|".join(m.nombre for m in modelos) + ")"
        self.ganadores: dict[str, int] = {m.nombre: 0 for m in modelos}

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:
        tareas = {
            asyncio.ensure_future(m.extraer(sistema, usuario, esquema, instruccion_formato)): m
            for m in self.modelos
        }
        errores: list[str] = []
        pendientes = set(tareas)
        try:
            while pendientes:
                hechas, pendientes = await asyncio.wait(
                    pendientes, return_when=asyncio.FIRST_COMPLETED
                )
                for t in hechas:
                    try:
                        resultado = t.result()
                    except Exception as e:  # noqa: BLE001
                        errores.append(f"{tareas[t].nombre}: {e}")
                        continue
                    self.ganadores[tareas[t].nombre] += 1
                    return resultado
        finally:
            for t in pendientes:
                t.cancel()
        raise LLMError("Ningún modelo respondió: " + "; ".join(errores))

    async def disponible(self) -> bool:
        for m in self.modelos:
            if hasattr(m, "disponible") and await m.disponible():
                return True
        return False

    async def calentar(self) -> None:
        await asyncio.gather(
            *(m.calentar() for m in self.modelos if hasattr(m, "calentar")),
            return_exceptions=True,
        )
