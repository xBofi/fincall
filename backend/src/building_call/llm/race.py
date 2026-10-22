"""Carrera de modelos: lanza la misma petición a varios proveedores y devuelve la primera válida.

Uso previsto: Workers AI (qwen3.8-27b, 100 % exactitud, p50 ~590 ms pero p95 ~1 200 ms por red)
contra Ollama local (qwen3.5:4b, 96 %, p50 ~610 ms, p95 ~870 ms). La carrera recorta la p95 a
costa de duplicar el gasto de tokens (Workers AI es barato; el local es gratis).
"""

from __future__ import annotations

import asyncio
from typing import Any

from building_call.llm.base import LanguageModel
from building_call.llm.ollama import LLMError


class RaceModel:
    """`preferred` (índice) + `extra_wait_ms`: si gana otro, se espera ese margen por el
    preferido (más exacto) antes de aceptar la respuesta del rápido."""

    def __init__(
        self, *models: LanguageModel, preferred: int | None = None, extra_wait_ms: int = 0
    ) -> None:
        if not models:
            raise ValueError("La carrera necesita al menos un modelo")
        self.models = list(models)
        self.preferred = models[preferred] if preferred is not None else None
        self.extra_wait_ms = extra_wait_ms
        self.name = "carrera(" + "|".join(m.name for m in models) + ")"
        self.winners: dict[str, int] = {m.name: 0 for m in models}

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        tasks = {
            asyncio.ensure_future(m.extract(system, user, schema, format_instruction)): m
            for m in self.models
        }
        errors: list[str] = []
        pending = set(tasks)
        try:
            while pending:
                done_tasks, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done_tasks:
                    try:
                        result = t.result()
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{tasks[t].name}: {e}")
                        continue
                    winner = tasks[t]
                    preferred_task = next(
                        (
                            tp
                            for tp, m in tasks.items()
                            if m is self.preferred and tp in pending
                        ),
                        None,
                    )
                    if preferred_task is not None and self.extra_wait_ms > 0:
                        try:
                            result = await asyncio.wait_for(
                                asyncio.shield(preferred_task), self.extra_wait_ms / 1000
                            )
                            winner = self.preferred  # type: ignore[assignment]
                        except Exception:  # noqa: BLE001 - timeout o fallo: vale el rápido
                            pass
                    self.winners[winner.name] += 1
                    return result
        finally:
            for t in pending:
                t.cancel()
        raise LLMError("Ningún modelo respondió: " + "; ".join(errors))

    async def available(self) -> bool:
        for m in self.models:
            if hasattr(m, "available") and await m.available():
                return True
        return False

    async def warm_up(self) -> None:
        await asyncio.gather(
            *(m.warm_up() for m in self.models if hasattr(m, "warm_up")),
            return_exceptions=True,
        )
