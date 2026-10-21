"""Prompts versionados de los nodos. Una sola versión por prompt; el identificador se registra
en las señales de confianza de cada llamada (auditoría). Cuando exista Langfuse, migran allí."""

from __future__ import annotations

from typing import Any

from building_call.domain.configuracion import CATEGORIA_NO_CLASIFICABLE, URGENCIAS

VERSION_CLASIFICACION = "clasificacion-v3"


def sistema_clasificacion(catalogo: list[str]) -> str:
    return (
        "Eres el clasificador de incidencias de una comunidad de propietarios en España. "
        "Clasifica la solicitud del vecino en exactamente una categoría del catálogo cerrado: "
        f"{', '.join(catalogo)}. Si no encaja claramente en ninguna, usa "
        f"'{CATEGORIA_NO_CLASIFICABLE}'. La categoría la decide el elemento que falla, nunca "
        "el sitio donde está ni lo que tiene al lado: el enchufe junto al ascensor es "
        "electricidad y la grieta en la pared de los buzones es albanileria. Si no dice qué "
        f"elemento falla ('está parado', 'no funciona'), usa '{CATEGORIA_NO_CLASIFICABLE}'. "
        "Urgencia: baja (molestia sin riesgo ni daño), media "
        "(afecta al uso normal), alta (daño en curso, servicio esencial interrumpido o riesgo "
        "material), emergencia (riesgo inmediato para personas). Escribe un resumen de una frase "
        "corta en español, en tercera persona, con los datos que dice el vecino y sin inventar."
    )


def esquema_clasificacion(catalogo: list[str]) -> dict[str, Any]:
    return {
        "title": "clasificacion",
        "type": "object",
        "properties": {
            "categoria": {"type": "string", "enum": catalogo},
            "urgencia": {"type": "string", "enum": list(URGENCIAS)},
            "resumen": {"type": "string"},
        },
        "required": ["categoria", "urgencia", "resumen"],
    }
