"""What the language model reads and writes: Spanish.

The callers are Spanish and so are the prompts, which were tuned and measured in Spanish (the
gold sets reach 96-100 %). The model therefore answers with Spanish JSON keys and Spanish codes
(`{"intencion": "humano"}`), and this module is the only place that translates them to the
English names the code uses. Reading accepts both languages, so an English answer also works.

Custom categories defined by an administrator have no Spanish name: they pass through as they
are.
"""

from __future__ import annotations

from typing import Any

#: JSON key the model writes -> domain field
KEYS: dict[str, str] = {
    "intencion": "intent",
    "confirma": "confirms",
    "direccion": "address",
    "portal": "entrance",
    "piso": "floor",
    "puerta": "door",
    "nombre": "name",
    "problema": "problem",
    "pregunta": "question",
    "categoria": "category",
    "urgencia": "urgency",
    "resumen": "summary",
    "actuacion": "work_type",
    "equipo": "equipment",
    "objecion": "objection",
    "respuesta": "reply",
}

#: Code the model writes -> domain code
INTENTS: dict[str, str] = {
    "continuar": "continue",
    "repetir": "repeat",
    "corregir": "correct",
    "humano": "human",
    "colgar": "hang_up",
    "fuera_ambito": "out_of_scope",
    "pregunta": "question",
}
URGENCIES: dict[str, str] = {
    "baja": "low",
    "media": "medium",
    "alta": "high",
    "emergencia": "emergency",
}
WORK_TYPES: dict[str, str] = {
    "reparacion": "repair",
    "reposicion": "replacement",
    "siniestro": "insurance_claim",
    "convivencia": "neighbour_dispute",
    "privativo": "private_unit",
}
CATEGORIES: dict[str, str] = {
    "fontaneria": "plumbing",
    "electricidad": "electrical",
    "ascensor": "elevator",
    "limpieza": "cleaning",
    "cerrajeria": "locksmith",
    "albanileria": "masonry",
    "pintura": "painting",
    "jardineria": "gardening",
    "mantenimiento_general": "general_maintenance",
    "administrativo": "administrative",
    "no_clasificable": "unclassifiable",
}

_TO_MODEL = {v: k for table in (INTENTS, URGENCIES, WORK_TYPES, CATEGORIES) for k, v in table.items()}


def to_model(code: str) -> str:
    """Domain code -> the Spanish code the model sees (`plumbing` -> `fontaneria`)."""
    return _TO_MODEL.get(code, code)


def from_model(table: dict[str, str], value: Any) -> Any:
    """The model's code -> domain code. English codes and unknown values pass through."""
    return table.get(value, value) if isinstance(value, str) else value


def read(data: dict[str, Any]) -> dict[str, Any]:
    """The model's JSON with domain keys and domain codes. English keys are also accepted."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        out[KEYS.get(key, key)] = value
    if "intent" in out:
        out["intent"] = from_model(INTENTS, out["intent"])
    if "urgency" in out:
        out["urgency"] = from_model(URGENCIES, out["urgency"])
    if "work_type" in out:
        out["work_type"] = from_model(WORK_TYPES, out["work_type"])
    if "category" in out:
        out["category"] = from_model(CATEGORIES, out["category"])
    return out
