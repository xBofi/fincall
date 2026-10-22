"""Contrato del modelo de lenguaje: siempre salida JSON validada contra un esquema."""

from __future__ import annotations

from typing import Any, Protocol


class LanguageModel(Protocol):
    name: str

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Devuelve un objeto JSON conforme a `schema` (JSON Schema)."""
        ...
