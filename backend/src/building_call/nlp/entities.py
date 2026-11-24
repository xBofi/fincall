"""Entities of a caller's turn with spaCy: address, entrance, floor, door, building, name, phone
and email.

A blank Spanish pipeline (tokenizer only, no statistical model to download) with an
`EntityRuler` of token patterns. Measured against the rule-based parser of the graph
(`_extract_home_regex`) on `evals/gold_interpretation.jsonl` by `evals/compare_nlp.py`: the
regex stays for entrance/floor/door, where it is tuned to STT output; spaCy adds what the regex
never had (address, building, name, phone, email). `extract_entities` combines both.

Values come back normalised like the graph does (`_normalize_floor`, `_normalize_door`), so
"tercero" and "3º" are both "3".
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# Labels -> Interpretation fields (BUILDING, PHONE and EMAIL have no field: contact details)
FIELDS = {
    "ADDRESS": "address",
    "ENTRANCE": "entrance",
    "FLOOR": "floor",
    "DOOR": "door",
    "NAME": "name",
    "BUILDING": "building",
    "PHONE": "phone",
    "EMAIL": "email",
}

_STREET_TYPES = [
    "calle", "c/", "avenida", "avda", "av", "plaza", "pza", "paseo", "ronda", "camino",
    "carrer", "avinguda", "plaça", "passeig", "travesía", "travesia", "glorieta",
    "street", "st", "avenue", "ave", "road", "rd", "lane", "square",
]  # fmt: skip
_ORDINAL_WORDS = [
    "primero", "primer", "primera", "segundo", "segunda", "tercero", "tercer", "tercera",
    "cuarto", "cuarta", "quinto", "quinta", "sexto", "sexta", "séptimo", "septimo", "octavo",
    "noveno", "décimo", "decimo", "bajo", "ático", "atico", "entresuelo", "principal",
]  # fmt: skip
_NUMBER_WORDS = [
    "uno", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
    "once", "doce", "trece", "catorce", "quince", "veinte", "treinta", "cuarenta", "cincuenta",
]  # fmt: skip
_DOOR_WORDS = ["izquierda", "derecha", "izq", "dcha", "centro"]
_LETTER = {"TEXT": {"REGEX": r"^[A-Ha-h]$"}}
_NUM = {"TEXT": {"REGEX": r"^\d{1,3}$"}}
_ORD_NUM = {"TEXT": {"REGEX": r"^\d{1,2}(º|ª|°|o|a)?$"}}
_NUMBERISH = {"LOWER": {"IN": _NUMBER_WORDS}}
_CAP = {"IS_TITLE": True}
_NAME_TOKEN = {"IS_TITLE": True, "IS_SENT_START": False}


def _patterns() -> list[dict[str, Any]]:
    street = {"LOWER": {"IN": _STREET_TYPES}}
    connector = {
        "LOWER": {"IN": ["de", "del", "la", "las", "los", "el", "dels", "d'", "of"]},
        "OP": "*",
    }
    number_mark = {
        "LOWER": {"IN": ["número", "numero", "nº", "n", "num", "number", ","]},
        "OP": "*",
    }
    pats: list[dict[str, Any]] = [
        # "Calle Mayor 12", "Avenida de la Alameda número 5", "12 Elm Street"
        {"label": "ADDRESS", "pattern": [street, connector, _CAP, {"IS_TITLE": True, "OP": "*"}, number_mark, _NUM]},
        {"label": "ADDRESS", "pattern": [street, connector, _CAP, {"IS_TITLE": True, "OP": "*"}, number_mark, _NUMBERISH]},
        {"label": "ADDRESS", "pattern": [_NUM, _CAP, {"IS_TITLE": True, "OP": "*"}, street]},
        # "portal 2", "escalera B"
        {"label": "ENTRANCE", "pattern": [{"LOWER": {"IN": ["portal", "escalera", "entrance"]}}, {"TEXT": {"REGEX": r"^(\d{1,2}|[A-Ha-h])$"}}]},
        {"label": "ENTRANCE", "pattern": [{"LOWER": {"IN": ["portal", "escalera"]}}, _NUMBERISH]},
        # "bloque 3", "edificio B", "torre 2"
        {"label": "BUILDING", "pattern": [{"LOWER": {"IN": ["bloque", "edificio", "torre", "block", "building", "fase"]}}, {"TEXT": {"REGEX": r"^(\d{1,2}|[A-Ha-h])$"}}]},
        # "3º B", "tercero A", "piso 4", "planta 2", "el bajo", "4th floor"
        {"label": "FLOOR", "pattern": [{"LOWER": {"IN": ["piso", "planta", "pis", "floor"]}}, {"TEXT": {"REGEX": r"^\d{1,2}$"}}]},
        {"label": "FLOOR", "pattern": [{"LOWER": {"IN": _ORDINAL_WORDS}}]},
        {"label": "FLOOR", "pattern": [_ORD_NUM, {"LOWER": {"IN": ["piso", "planta", "floor"]}, "OP": "?"}]},
        {"label": "FLOOR", "pattern": [{"TEXT": {"REGEX": r"^\d{1,2}(st|nd|rd|th)$"}}, {"LOWER": "floor"}]},
        {"label": "DOOR", "pattern": [{"LOWER": {"IN": ["puerta", "letra", "door", "porta"]}}, {"TEXT": {"REGEX": r"^([A-Ha-h]|\d{1,2})$"}}]},
        {"label": "DOOR", "pattern": [{"LOWER": {"IN": ["puerta"]}}, {"LOWER": {"IN": _DOOR_WORDS}}]},
        # "me llamo Carmen", "soy Luis", "my name is Sarah", "em dic Jordi"
        {"label": "NAME", "pattern": [{"LOWER": "me"}, {"LOWER": "llamo"}, _NAME_TOKEN, {"IS_TITLE": True, "OP": "?"}]},
        {"label": "NAME", "pattern": [{"LOWER": "soy"}, _NAME_TOKEN, {"IS_TITLE": True, "OP": "?"}]},
        {"label": "NAME", "pattern": [{"LOWER": "my"}, {"LOWER": "name"}, {"LOWER": "is"}, _NAME_TOKEN]},
        {"label": "NAME", "pattern": [{"LOWER": "em"}, {"LOWER": "dic"}, _NAME_TOKEN]},
        {"label": "EMAIL", "pattern": [{"LIKE_EMAIL": True}]},
        {"label": "PHONE", "pattern": [{"TEXT": {"REGEX": r"^\+?\d{9,12}$"}}]},
        {"label": "PHONE", "pattern": [{"TEXT": {"REGEX": r"^\+?\d{2,3}$"}}, {"TEXT": {"REGEX": r"^\d{2,3}$"}}, {"TEXT": {"REGEX": r"^\d{2,3}$"}}, {"TEXT": {"REGEX": r"^\d{2,3}$"}, "OP": "?"}]},
    ]  # fmt: skip
    return pats


@lru_cache(maxsize=1)
def pipeline() -> Any:
    """spaCy pipeline (cached). Raises ImportError without the `nlp` extra."""
    import spacy

    nlp = spacy.blank("es")
    ruler = nlp.add_pipe("entity_ruler", config={"overwrite_ents": True})
    ruler.add_patterns(_patterns())
    return nlp


# Words that open a name pattern but are not names ("soy del tercero", "soy Vecino")
_NOT_NAMES = {
    "del",
    "de",
    "el",
    "la",
    "vecino",
    "vecina",
    "propietario",
    "propietaria",
    "inquilino",
}


def _value(label: str, text: str) -> str | None:
    from building_call.graph.nodes import (
        _normalize_door,
        _normalize_entrance,
        _normalize_floor,
        _numbers_to_digits,
    )

    words = text.split()
    if label == "NAME":
        tail = [w for w in words if w[:1].isupper() and w.lower() not in _NOT_NAMES]
        # "me llamo Carmen" -> "Carmen"; drop the opening verb
        name = " ".join(
            w
            for w in tail
            if w.lower() not in {"me", "llamo", "soy", "my", "name", "is", "em", "dic"}
        )
        return name or None
    if label == "ENTRANCE":
        return _normalize_entrance(words[-1]) or _normalize_entrance(
            _numbers_to_digits(words[-1].lower())
        )
    if label == "FLOOR":
        word = next(
            (w for w in words if w.lower() not in {"piso", "planta", "pis", "floor"}), words[0]
        )
        word = re.sub(r"(st|nd|rd|th)$", "", word)
        return _normalize_floor(word)
    if label == "DOOR":
        return _normalize_door(words[-1])
    if label == "BUILDING":
        return words[-1].upper()
    if label == "PHONE":
        return re.sub(r"\D", "", text)
    return text.strip(" ,")


def spacy_entities(text: str) -> dict[str, str]:
    """Fields found by the spaCy EntityRuler alone (first match per field)."""
    doc = pipeline()(text)
    found: dict[str, str] = {}
    for ent in doc.ents:
        field = FIELDS.get(ent.label_)
        if field and field not in found:
            value = _value(ent.label_, ent.text)
            if value:
                found[field] = value
    # "3º B" / "tercero A": a lone letter right after a floor is the door
    if "door" not in found:
        for ent in doc.ents:
            if ent.label_ == "FLOOR" and ent.end < len(doc):
                nxt = doc[ent.end]
                if re.fullmatch(r"[A-Ha-h]", nxt.text):
                    found["door"] = nxt.text.upper()
                    break
    return found


def regex_entities(text: str) -> dict[str, str]:
    """The graph's current rule-based parser, for comparison."""
    from building_call.graph.nodes import _extract_home_regex

    entrance, floor, door = _extract_home_regex(text)
    return {k: v for k, v in (("entrance", entrance), ("floor", floor), ("door", door)) if v}


def extract_entities(text: str) -> dict[str, str]:
    """Combined: the regex for entrance/floor/door (it wins on those, see the module docstring)
    and spaCy for the rest."""
    found = spacy_entities(text)
    found.update(regex_entities(text))
    return found
