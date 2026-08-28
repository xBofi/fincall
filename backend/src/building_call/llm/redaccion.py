"""Redacción de la respuesta del asistente: paráfrasis controlada.

El grafo decide QUÉ hay que decir (la plantilla trae los hechos y la siguiente pregunta); el
modelo decide CÓMO decirlo, respondiendo a lo que el vecino acaba de decir. En código se
comprueba que la redacción conserva los hechos obligatorios (números, referencias, nombres,
opciones); si no, o si el modelo falla o tarda, se usa la plantilla tal cual.
"""

from __future__ import annotations

import asyncio
import re
from time import perf_counter
from typing import Any

from building_call.llm.base import ModeloLenguaje

VERSION_REDACCION = "redaccion-v1"

SISTEMA_REDACCION = """Eres la voz de un asistente telefónico de incidencias de una comunidad de vecinos (España). Hablas por teléfono: frases cortas, tono cercano y profesional, trato de usted. Recibes lo último que ha dicho el vecino, los datos que ya conoces y el MENSAJE OBLIGATORIO que debes transmitir.

Reglas:
- Reformula el mensaje obligatorio para que responda de forma natural a lo que el vecino acaba de decir (reconoce lo que dijo si procede: "Entendido, en el primero A", "Vale, una fuga").
- Conserva TODOS los hechos del mensaje obligatorio: direcciones, pisos, puertas, nombres, referencias, plazos y opciones ofrecidas. No añadas datos, promesas ni horas que no estén en él.
- Si el mensaje obligatorio contiene una pregunta, tu respuesta debe terminar con esa misma pregunta (puedes reformularla, no cambiar lo que pide).
- Máximo dos frases más la pregunta. Nada de listas, emojis ni comillas.
- Si el vecino dijo algo que no tiene que ver con lo que se le pidió, reconócelo brevemente y redirige con amabilidad, sin regañar.
- No des por confirmado ni agradezcas lo que estás preguntando: si preguntas "¿es correcto?", aún no lo sabes.

Formato: JSON en una línea con la clave "respuesta"."""

_TOKENS_OBLIGATORIOS = re.compile(
    r"\b(\d+[ºª]?|inc-[a-z0-9]+|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)\b"
)


def hechos_obligatorios(plantilla: str) -> set[str]:
    """Números, referencias y nombres propios de la plantilla que deben sobrevivir."""
    hechos: set[str] = set()
    for m in _TOKENS_OBLIGATORIOS.finditer(plantilla):
        token = m.group(1)
        if token in {
            "He",
            "Le",
            "Lo",
            "La",
            "Se",
            "Si",
            "No",
            "Gracias",
            "Perfecto",
            "Hola",
            "De",
            "El",
            "En",
            "Por",
            "Todavía",
            "Dígame",
            "Indíqueme",
            "Perdone",
            "Claro",
            "Entendido",
            "Buenas",
        }:
            continue
        if token[0].isdigit() or token.startswith("inc-") or " " in token or len(token) > 3:
            hechos.add(token)
    return hechos


def conserva_hechos(plantilla: str, redaccion: str) -> bool:
    r = redaccion.lower()
    for h in hechos_obligatorios(plantilla):
        # Los ordinales pueden decirse "3º" o "3"; los nombres compuestos, por partes
        variantes = {h.lower(), h.lower().rstrip("ºª"), *(p.lower() for p in h.split())}
        if not any(v and v in r for v in variantes):
            return False
    if "?" in plantilla and "?" not in redaccion:
        return False
    return True


async def redactar(
    llm: ModeloLenguaje,
    plantilla: str,
    entrada_vecino: str | None,
    datos: dict[str, Any],
    timeout_s: float = 0.9,
) -> tuple[str, dict[str, Any]]:
    """Devuelve (texto_final, métrica). Si algo falla, texto_final es la plantilla."""
    inicio = perf_counter()
    conocidos = ", ".join(f"{k}: {v}" for k, v in datos.items() if v)
    usuario = (
        f'Último mensaje del vecino: """{entrada_vecino or "(inicio de la llamada)"}"""\n'
        f"Datos ya conocidos: {conocidos or 'ninguno'}\n"
        f'MENSAJE OBLIGATORIO: """{plantilla}"""'
    )
    esquema = {
        "title": "redaccion",
        "type": "object",
        "properties": {"respuesta": {"type": "string"}},
    }
    try:
        salida = await asyncio.wait_for(
            llm.extraer(SISTEMA_REDACCION, usuario, esquema, instruccion_formato=""), timeout_s
        )
        texto = str(salida.get("respuesta") or "").strip()
    except Exception as e:  # noqa: BLE001 - la plantilla siempre es válida
        return plantilla, {
            "redaccion_ms": int((perf_counter() - inicio) * 1000),
            "redaccion": f"fallo:{type(e).__name__}",
        }
    ms = int((perf_counter() - inicio) * 1000)
    if not texto or len(texto) > 2.5 * len(plantilla) + 80 or not conserva_hechos(plantilla, texto):
        return plantilla, {"redaccion_ms": ms, "redaccion": "plantilla:hechos_no_conservados"}
    return texto, {"redaccion_ms": ms, "redaccion": "llm"}
