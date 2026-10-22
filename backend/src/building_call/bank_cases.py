"""Casos del banco de pruebas marcados por calidad desde el expediente de una llamada.

Convertir cada fallo real en un caso de `evals/gold_interpretation.jsonl` o
`evals/gold_classification.jsonl` es lo que más ha mejorado al asistente. Aquí se valida lo que
el revisor dice que el bot debería haber entendido y se construye el caso a partir del turno
guardado, sin datos personales: nunca el teléfono y el nombre del vecino sustituido por uno
ficticio. `evals/export_cases.py` los pasa al formato del banco.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from building_call.domain.config import URGENCIES
from building_call.domain.entities import WORK_TYPES
from building_call.llm.interpretation import INTENTS

Bank = Literal["interpretation", "classification"]

# Las claves de `Interpretation` que el banco puede esperar (las de `compare_models.py`)
EXPECTED_KEYS: tuple[str, ...] = (
    "intent", "confirms", "address", "entrance", "floor", "door", "name", "problem",
    "question", "category", "urgency", "summary", "work_type", "equipment", "objection",
)  # fmt: skip

_ENUMS: dict[str, tuple[str, ...]] = {
    "intent": INTENTS,
    "urgency": URGENCIES,
    "work_type": WORK_TYPES,
}

# Nodos donde aún no se conoce la finca: el turno real no llevaba la lista de equipos
_NODES_WITHOUT_ESTATE = {"start", "identify_estate"}

FAKE_NAME = ("Carmen", "Ruiz", "Gómez")
FAKE_PHONE = "600 000 000"
_PHONE = re.compile(r"(?<![\d+])(?:\+\s?34[\s.-]?)?\d(?:[\s.-]?\d){8}(?!\d)")
_PARTICLES = {"de", "del", "la", "las", "los", "y", "i"}


class BankCaseInput(BaseModel):
    """Lo que manda el revisor al marcar un turno del vecino."""

    turn: int
    expected: dict[str, Any]
    note: str | None = Field(default=None, max_length=2000)
    bank: Bank = "interpretation"
    # Si el vecino cortó al bot, lo que llegó a oír (ledger de entrega)
    heard_text: str | None = Field(default=None, max_length=2000)
    # Por si la pregunta deducida del turno anterior no es la que contestaba
    question: str | None = Field(default=None, max_length=2000)


class InvalidCase(ValueError):
    pass


def _valid_value(key: str, value: Any) -> bool:
    if value is None or value == "*":
        return True
    if key == "confirms":
        return isinstance(value, bool)
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        return False
    return key not in _ENUMS or value in _ENUMS[key]


def validate_expected(expected: dict[str, Any], bank: Bank) -> None:
    """Como lo compara `compare_models.py`: un valor, "*" (cualquiera), null (sin dato) o una
    lista de alternativas. Los enumerados se validan aquí y no en el modelo."""
    if not expected:
        raise InvalidCase("Indica al menos qué debería haber entendido el asistente")
    for key, value in expected.items():
        if key not in EXPECTED_KEYS:
            raise InvalidCase(f"Clave desconocida: {key}")
        values = value if isinstance(value, list) else [value]
        if not values or not all(_valid_value(key, v) for v in values):
            raise InvalidCase(f"Valor no válido para {key}: {value!r}")
    if bank == "classification":
        if not isinstance(expected.get("category"), str) or expected["category"] == "*":
            raise InvalidCase("Un caso de clasificación necesita una sola categoría")
        urgencies = expected.get("urgency")
        urgencies = urgencies if isinstance(urgencies, list) else [urgencies]
        if not all(u in URGENCIES for u in urgencies):
            raise InvalidCase("Un caso de clasificación necesita la urgencia (o varias válidas)")


def strip_phones(text: str) -> str:
    return _PHONE.sub(FAKE_PHONE, text)


def pseudonymize(text: str, real_name: str | None) -> str:
    """Cambia el nombre del vecino por uno ficticio, palabra a palabra, sin tocar el resto."""
    if not real_name:
        return text
    parts = [p for p in real_name.split() if len(p) >= 2 and p.lower() not in _PARTICLES]
    for i, part in enumerate(parts):
        placeholder = FAKE_NAME[min(i, len(FAKE_NAME) - 1)]
        text = re.sub(rf"\b{re.escape(part)}\b", placeholder, text, flags=re.IGNORECASE)
    return text


def _clean(value: Any, real_name: str | None) -> Any:
    if isinstance(value, str):
        return strip_phones(pseudonymize(value, real_name))
    if isinstance(value, list):
        return [_clean(v, real_name) for v in value]
    return value


def build_case(
    utterance: BankCaseInput,
    turns: list[dict[str, Any]],
    *,
    real_name: str | None,
    equipment_list: list[tuple[str, str]] | None,
) -> dict[str, Any]:
    """Campos del caso a partir de los turnos guardados de la llamada. Lanza `LookupError` si el
    turno no existe y `InvalidCase` si no es un turno del vecino o lo esperado no vale."""
    validate_expected(utterance.expected, utterance.bank)
    turn = next((t for t in turns if t["turn"] == utterance.turn), None)
    if turn is None:
        raise LookupError(f"La llamada no tiene turno {utterance.turn}")
    if not (turn.get("caller") or "").strip():
        raise InvalidCase("Ese turno no tiene nada dicho por el vecino")
    previous = next((t for t in turns if t["turn"] == utterance.turn - 1), None)
    messages = (previous or {}).get("assistant") or []
    question = utterance.question or (messages[-1] if messages else None)
    node = turn.get("initial_node") or "start"
    return {
        "turn": utterance.turn,
        "bank": utterance.bank,
        "node": node,
        "question": _clean(question, real_name),
        "heard_text": _clean(utterance.heard_text, real_name) if utterance.heard_text else None,
        "utterance": _clean(turn["caller"], real_name),
        # El grafo interpreta siempre con el catálogo (una sola llamada clasifica también)
        "catalog": True,
        "equipment_list": [list(e) for e in equipment_list] if equipment_list and node not in _NODES_WITHOUT_ESTATE else None,
        "expected": {k: _clean(v, real_name) for k, v in utterance.expected.items()},
        "note": _clean(utterance.note, real_name),
    }
