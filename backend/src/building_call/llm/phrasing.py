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

from building_call.llm import wire
from building_call.llm.base import LanguageModel

PHRASING_VERSION = "redaccion-v1"

PHRASING_SYSTEM = """Eres la voz de un asistente telefónico de incidencias de una comunidad de vecinos (España). Hablas por teléfono: frases cortas, tono cercano y profesional, trato de usted. Recibes lo último que ha dicho el vecino, los datos que ya conoces y el MENSAJE OBLIGATORIO que debes transmitir.

Reglas:
- Reformula el mensaje obligatorio para que responda de forma natural a lo que el vecino acaba de decir (reconoce lo que dijo si procede: "Entendido, en el primero A", "Vale, una fuga").
- Conserva TODOS los hechos del mensaje obligatorio: direcciones, pisos, puertas, nombres, referencias, plazos y opciones ofrecidas. No añadas datos, promesas ni horas que no estén en él.
- Si el mensaje obligatorio contiene una pregunta, tu respuesta debe terminar con esa misma pregunta (puedes reformularla, no cambiar lo que pide).
- Máximo dos frases más la pregunta. Nada de listas, emojis ni comillas.
- Si el vecino dijo algo que no tiene que ver con lo que se le pidió, reconócelo brevemente y redirige con amabilidad, sin regañar.
- No des por confirmado ni agradezcas lo que estás preguntando: si preguntas "¿es correcto?", aún no lo sabes. Prohibido "gracias por confirmar" antes de que el vecino confirme.
- No repitas datos que no estén en el mensaje obligatorio (aunque los conozcas): la pregunta debe ser corta.

Formato: JSON en una línea con la clave "respuesta"."""

_REQUIRED_TOKENS = re.compile(
    r"\b(\d+[ºª]?|inc-[a-z0-9]+|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)\b"
)


def required_facts(template: str) -> set[str]:
    """Números, referencias y nombres propios de la plantilla que deben sobrevivir."""
    facts: set[str] = set()
    for m in _REQUIRED_TOKENS.finditer(template):
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
            facts.add(token)
    return facts


def keeps_facts(template: str, phrasing: str) -> bool:
    r = phrasing.lower()
    for h in required_facts(template):
        # Los ordinales pueden decirse "3º" o "3"; los nombres compuestos, por partes
        variants = {h.lower(), h.lower().rstrip("ºª"), *(p.lower() for p in h.split())}
        if not any(v and v in r for v in variants):
            return False
    if "?" in template and "?" not in phrasing:
        return False
    return True


async def phrase(
    llm: LanguageModel,
    template: str,
    caller_utterance: str | None,
    data: dict[str, Any],
    timeout_s: float = 0.75,
) -> tuple[str, dict[str, Any]]:
    """Devuelve (texto_final, métrica). Si algo falla, texto_final es la plantilla."""
    start = perf_counter()
    known = ", ".join(f"{k}: {v}" for k, v in data.items() if v)
    user = (
        f'Último mensaje del vecino: """{caller_utterance or "(inicio de la llamada)"}"""\n'
        f"Datos ya conocidos: {known or 'ninguno'}\n"
        f'MENSAJE OBLIGATORIO: """{template}"""'
    )
    schema = {
        "title": "phrasing",
        "type": "object",
        "properties": {"respuesta": {"type": "string"}},
    }
    try:
        output = await asyncio.wait_for(
            llm.extract(PHRASING_SYSTEM, user, schema, format_instruction=""), timeout_s
        )
        text = str(wire.read(output).get("reply") or "").strip()
    except Exception as e:  # noqa: BLE001 - la plantilla siempre es válida
        return template, {
            "phrasing_ms": int((perf_counter() - start) * 1000),
            "phrasing": f"failure:{type(e).__name__}",
        }
    ms = int((perf_counter() - start) * 1000)
    if not text or len(text) > 2.5 * len(template) + 80 or not keeps_facts(template, text):
        return template, {"phrasing_ms": ms, "phrasing": "template:facts_not_kept"}
    return text, {"phrasing_ms": ms, "phrasing": "llm"}
