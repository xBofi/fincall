"""Entities of a caller's turn with spaCy: address, entrance, floor, door, building, name, phone
and email, in the call's language.

One blank pipeline per language (`spacy.blank(code)`: its tokenizer only, no statistical model
to download) with an `EntityRuler` of token patterns. The home words come from the language's
locale (`i18n/locales/<code>.toml`: entrance/floor/door markers and spoken floors), so a new
language gets entrance/floor/door patterns with no code. What the locales do not describe
(street types, how people introduce themselves, building words) is in `_EXTRA` below: one row
per language; a language without a row still gets the locale patterns plus phone and email.

`regex_entities` is the graph's rule-based parser (`Parser.extract_home` of the language);
`extract_entities` combines both: the regex for entrance/floor/door, spaCy for the rest. The
comparison that chose this is in `evals/compare_nlp.py --entities-only` and the README.

Values come back normalised by the language's parser, as the graph does ("tercero", "3º" and
"third" are all "3").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from building_call import i18n

# Labels -> fields (BUILDING, PHONE and EMAIL have no Interpretation field: contact details)
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


@dataclass(frozen=True)
class _Extra:
    """Per-language words the locale files do not carry."""

    street_types: tuple[str, ...] = ()
    #: Words between the street type and its name: "Avenida de la Alameda"
    street_connectors: tuple[str, ...] = ()
    #: The street type goes after the name: "12 Elm Street"
    street_type_last: bool = False
    number_marks: tuple[str, ...] = ()
    #: How a caller says their name: ("me", "llamo") -> "me llamo Carmen"
    name_intros: tuple[tuple[str, ...], ...] = ()
    #: Words after a name intro that are not a name: "soy del tercero", "soy vecino"
    not_names: tuple[str, ...] = ()
    building_markers: tuple[str, ...] = ()


_EXTRA: dict[str, _Extra] = {
    "es": _Extra(
        street_types=(
            "calle",
            "c/",
            "avenida",
            "avda",
            "av",
            "plaza",
            "pza",
            "paseo",
            "ronda",
            "camino",
            "travesía",
            "travesia",
            "glorieta",
            "carrer",
            "avinguda",
            "plaça",
            "passeig",
        ),
        street_connectors=("de", "del", "la", "las", "los", "el"),
        number_marks=("número", "numero", "nº", "n", "num", ","),
        name_intros=(("me", "llamo"), ("soy",), ("mi", "nombre", "es")),
        not_names=(
            "del",
            "de",
            "el",
            "la",
            "vecino",
            "vecina",
            "propietario",
            "propietaria",
            "inquilino",
            "inquilina",
            "yo",
        ),
        building_markers=("bloque", "edificio", "torre", "fase"),
    ),
    "en": _Extra(
        street_types=(
            "street",
            "st",
            "avenue",
            "ave",
            "road",
            "rd",
            "lane",
            "ln",
            "boulevard",
            "blvd",
            "drive",
            "dr",
            "square",
            "place",
            "court",
            "way",
        ),
        street_type_last=True,
        number_marks=("number", "no", ","),
        # The tokenizer splits "I'm" and "name's"
        name_intros=(
            ("my", "name", "is"),
            ("my", "name", "'s"),
            ("this", "is"),
            ("i", "'m"),
            ("i", "am"),
        ),
        not_names=("the", "a", "calling", "from", "in", "on", "at", "your", "here", "not"),
        building_markers=("block", "building", "tower"),
    ),
    "ca": _Extra(
        street_types=("carrer", "avinguda", "plaça", "passeig", "ronda", "camí"),
        street_connectors=("de", "del", "la", "les", "els", "d'"),
        number_marks=("número", "numero", "nº", ","),
        name_intros=(("em", "dic"), ("sóc",), ("soc",)),
        not_names=("del", "de", "el", "la", "veí", "veïna"),
        building_markers=("bloc", "edifici", "torre"),
    ),
}

_NUM = {"TEXT": {"REGEX": r"^\d{1,4}$"}}
_SHORT = {"TEXT": {"REGEX": r"^(\d{1,2}|[A-Ha-h])$"}}
_CAP = {"IS_TITLE": True}


def _lower_in(words: Any) -> dict[str, Any]:
    return {"LOWER": {"IN": sorted({w.lower() for w in words})}}


def _patterns(language: i18n.Language) -> list[dict[str, Any]]:
    extra = _EXTRA.get(language.code, _Extra())
    home = language.home
    numbers = list(language.lexicon.numbers)
    floor_words = [w for w in home.floors if w not in home.floor_words_not_in_text]
    pats: list[dict[str, Any]] = [
        {"label": "EMAIL", "pattern": [{"LIKE_EMAIL": True}]},
        {"label": "PHONE", "pattern": [{"TEXT": {"REGEX": r"^\+?\d{9,12}$"}}]},
        {"label": "PHONE", "pattern": [{"TEXT": {"REGEX": r"^\+?\d{2,3}$"}}] + [{"TEXT": {"REGEX": r"^\d{2,4}$"}}] * 2
         + [{"TEXT": {"REGEX": r"^\d{2,3}$"}, "OP": "?"}]},
    ]  # fmt: skip
    if home.entrance_markers:
        pats.append({"label": "ENTRANCE", "pattern": [_lower_in(home.entrance_markers), _SHORT]})
        if numbers:
            pats.append(
                {
                    "label": "ENTRANCE",
                    "pattern": [_lower_in(home.entrance_markers), _lower_in(numbers)],
                }
            )
    if home.floor_markers:
        pats.append(
            {
                "label": "FLOOR",
                "pattern": [_lower_in(home.floor_markers), {"TEXT": {"REGEX": r"^\d{1,2}$"}}],
            }
        )
    if floor_words:
        # "tercero", "third floor"
        pats.append(
            {
                "label": "FLOOR",
                "pattern": [
                    _lower_in(floor_words),
                    {**_lower_in(home.floor_markers or ["-"]), "OP": "?"},
                ],
            }
        )
    # "3º", "3ª" (written forms) and "3rd floor"
    pats.append({"label": "FLOOR", "pattern": [{"TEXT": {"REGEX": r"^\d{1,2}(º|ª|°)$"}}]})
    if home.door_markers:
        pats.append({"label": "DOOR", "pattern": [_lower_in(home.door_markers), _SHORT]})
        if home.doors:
            pats.append(
                {"label": "DOOR", "pattern": [_lower_in(home.door_markers), _lower_in(home.doors)]}
            )
    # "3A" (US style): floor and door in one token
    pats.append({"label": "FLOOR_DOOR", "pattern": [{"TEXT": {"REGEX": r"^\d{1,2}[A-Ha-h]$"}}]})
    if extra.street_types:
        street = _lower_in(extra.street_types)
        name = [_CAP, {"IS_TITLE": True, "OP": "*"}]
        number = [_NUM] + ([_lower_in(numbers)] if numbers else [])
        if extra.street_type_last:
            # "12 Elm Street", "twelve Main Street", "Main Street, number twelve"
            mark = {**_lower_in(extra.number_marks or [","]), "OP": "*"}
            for n in number:
                pats.append({"label": "ADDRESS", "pattern": [n, *name, street]})
                pats.append({"label": "ADDRESS", "pattern": [*name, street, mark, n]})
        else:
            connector = {**_lower_in(extra.street_connectors or ["-"]), "OP": "*"}
            mark = {**_lower_in(extra.number_marks or [","]), "OP": "*"}
            for n in number:
                pats.append({"label": "ADDRESS", "pattern": [street, connector, *name, mark, n]})
    for intro in extra.name_intros:
        pats.append(
            {"label": "NAME", "pattern": [*({"LOWER": w} for w in intro),
             {"IS_TITLE": True}, {"IS_TITLE": True, "OP": "?"}]}
        )  # fmt: skip
    if extra.building_markers:
        pats.append({"label": "BUILDING", "pattern": [_lower_in(extra.building_markers), _SHORT]})
    return pats


@cache
def pipeline(code: str | None = None) -> Any:
    """spaCy pipeline of the language (cached). Raises ImportError without the `nlp` extra."""
    import spacy

    language = i18n.get(code)
    try:
        nlp = spacy.blank(language.code)
    except Exception:  # noqa: BLE001 - spaCy has no tokenizer for it: the multilingual one
        nlp = spacy.blank("xx")
    ruler: Any = nlp.add_pipe("entity_ruler", config={"overwrite_ents": True})
    ruler.add_patterns(_patterns(language))
    return nlp


def _value(label: str, text: str, language: i18n.Language) -> dict[str, str]:
    parser = i18n.parser_for(language.code)
    words = text.split()
    extra = _EXTRA.get(language.code, _Extra())
    if label == "NAME":
        intro = {w for i in extra.name_intros for w in i}
        name = [w for w in words if w.lower() not in intro]
        if not name or name[0].lower() in extra.not_names:
            return {}
        return {"name": " ".join(name)}
    if label == "FLOOR_DOOR":
        return {"floor": text[:-1], "door": text[-1].upper()}
    if label == "ENTRANCE":
        value = parser.normalize_entrance(words[-1]) or parser.normalize_entrance(
            parser.numbers_to_digits(parser.normalize(words[-1]))
        )
        return {"entrance": value} if value else {}
    if label == "FLOOR":
        markers = {m.lower() for m in language.home.floor_markers}
        word = next((w for w in words if w.lower() not in markers), words[0])
        value = parser.normalize_floor(re.sub(r"[º°ª]$", "", word))
        return {"floor": value} if value else {}
    if label == "DOOR":
        value = parser.normalize_door(words[-1])
        return {"door": value} if value else {}
    if label == "BUILDING":
        return {"building": words[-1].upper()}
    if label == "PHONE":
        return {"phone": re.sub(r"\D", "", text)}
    return {FIELDS[label]: text.strip(" ,")}


def spacy_entities(text: str, language: str | None = None) -> dict[str, str]:
    """Fields found by the spaCy EntityRuler alone (first match per field)."""
    lang = i18n.get(language or i18n.active_code())
    doc = pipeline(lang.code)(text)
    found: dict[str, str] = {}
    for ent in doc.ents:
        # Names by token: "I'm Marta" is the tokens "I", "'m", "Marta"
        text = " ".join(t.text for t in ent) if ent.label_ == "NAME" else ent.text
        for field, value in _value(ent.label_, text, lang).items():
            found.setdefault(field, value)
    # "3º B" / "tercero A": a lone letter right after a floor is the door
    if "door" not in found:
        for ent in doc.ents:
            if ent.label_ == "FLOOR" and ent.end < len(doc):
                nxt = doc[ent.end].text.rstrip(".,")
                if re.fullmatch(r"[A-Ha-h]", nxt) and nxt.lower() not in lang.home.one_letter_words:
                    found["door"] = nxt.upper()
                    break
    return found


def regex_entities(text: str, language: str | None = None) -> dict[str, str]:
    """The graph's current rule-based parser, for comparison."""
    entrance, floor, door = i18n.parser_for(language or i18n.active_code()).extract_home(text)
    return {k: v for k, v in (("entrance", entrance), ("floor", floor), ("door", door)) if v}


def extract_entities(text: str, language: str | None = None) -> dict[str, str]:
    """Combined: the regex for entrance/floor/door, spaCy for the rest."""
    found = spacy_entities(text, language)
    found.update(regex_entities(text, language))
    return found
