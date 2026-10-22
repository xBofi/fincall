"""Contrato del modelo de lenguaje: siempre salida JSON validada contra un esquema."""

from __future__ import annotations

from typing import Any, Protocol


class ModeloLenguaje(Protocol):
    nombre: str

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:
        """Devuelve un objeto JSON conforme a `esquema` (JSON Schema)."""
        ...
