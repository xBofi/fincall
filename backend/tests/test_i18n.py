"""The language registry: every locale file is complete and consistent, and each language's
deterministic rules read what its callers say."""

from __future__ import annotations

import re

import pytest

from building_call import i18n
from building_call.graph import templates as tpl
from building_call.i18n.text import basic_normalize

LANGUAGES = i18n.supported()
_VARIABLE = re.compile(r"\{(\w+)\}")


def test_spanish_and_english_are_registered() -> None:
    assert {"es", "en"} <= set(LANGUAGES)
    assert i18n.get("en").locale == "en-US"  # US English: the main clients are in the US
    assert i18n.get("es").locale == "es-ES"


@pytest.mark.parametrize("code", ["en-US", "EN", "en_us", "es-419"])
def test_codes_are_normalized(code: str) -> None:
    assert i18n.get(code).code == code[:2].lower()


def test_unknown_language_raises() -> None:
    with pytest.raises(i18n.UnknownLanguage):
        i18n.get("xx")
    assert i18n.normalize_code("xx-YY") is None


def test_default_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from building_call.settings import settings

    monkeypatch.setattr(settings, "language", "en")
    assert i18n.default_code() == "en"
    monkeypatch.setattr(settings, "language", "klingon")
    assert i18n.default_code() == "es"  # an unsupported value falls back to the source language


def test_use_sets_the_active_language_and_restores_it() -> None:
    before = i18n.active_code()
    with i18n.use("en"):
        assert i18n.active_code() == "en"
        with i18n.use("es"):
            assert i18n.current().code == "es"
        assert i18n.active_code() == "en"
    assert i18n.active_code() == before


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_template_translation_keeps_variables_and_question(code: str) -> None:
    """A translation that drops `{address}` would crash the call; one without "?" would make
    the phrasing step discard every paraphrase."""
    language = i18n.get(code)
    if code == i18n.SOURCE_LANGUAGE:
        assert not language.templates  # the source text lives in `graph/templates.py`
        return
    missing = set(tpl.CATALOG) - set(language.templates)
    assert not missing, f"{code} does not translate {sorted(missing)}"
    unknown = set(language.templates) - set(tpl.CATALOG)
    assert not unknown, f"{code} translates ids that do not exist: {sorted(unknown)}"
    for id_, text in language.templates.items():
        assert set(_VARIABLE.findall(text)) == set(tpl.CATALOG[id_].variables), id_
        assert tpl.validate(id_, text) == [], (id_, tpl.validate(id_, text))


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_language_has_the_phrases_the_graph_builds_with(code: str) -> None:
    language = i18n.get(code)
    assert {"_", "unclassifiable"} <= set(language.detail_questions)
    assert set(language.home.missing) == {"door", "floor", "both"}
    assert {"entrances", "floors", "floor", "doors", "door", "sentence"} <= set(
        language.home.describe
    )
    assert language.lexicon.yes and language.lexicon.no
    assert language.tts.browser_lang


def test_render_uses_the_active_language() -> None:
    with i18n.use("en"):
        assert tpl.render("collect_request.greeting") == "Perfect. "
    with i18n.use("es"):
        assert tpl.render("collect_request.greeting") == "Perfecto. "


def test_overrides_are_per_language() -> None:
    try:
        tpl.apply("global.filler", "Take your time, no rush.", 3, "en")
        with i18n.use("en"):
            assert tpl.render("global.filler") == "Take your time, no rush."
            assert tpl.render("global.filler").version == 3
        with i18n.use("es"):
            assert tpl.render("global.filler") == tpl.CATALOG["global.filler"].text
        assert tpl.storage_key("global.filler", "en") == "global.filler@en"
        assert tpl.storage_key("global.filler", "es") == "global.filler"
        tpl.load({"global.filler@en": ("Sure, take your time.", 4), "nope@en": ("x", 1)})
        with i18n.use("en"):
            assert tpl.render("global.filler") == "Sure, take your time."
    finally:
        tpl.load({})


# ------------------------------------------------------------------------------- normalization


def test_normalization_keeps_every_script() -> None:
    """Latin-only normalization emptied Arabic, Hindi and Urdu sentences."""
    assert basic_normalize("¿Sí, el 3º B?") == "si el 3 b"
    assert basic_normalize("مَرْحَبًا ٣") == "مرحبا 3"  # harakat dropped, Arabic-Indic digit
    assert basic_normalize("नमस्ते ३") == "नमस्ते 3"  # vowel signs kept, Devanagari digit
    assert basic_normalize("ہاں، ٹھیک ہے") == "ہاں ٹھیک ہے"
    assert basic_normalize("it's", join_apostrophes=True) == "its"


# ------------------------------------------------------------------------------- per language


@pytest.mark.parametrize(
    ("code", "text", "expected"),
    [
        ("es", "Sí, correcto", True),
        ("es", "S.", True),  # what the STT makes of a short "sí"
        ("es", "No, es el tercero", False),
        ("es", "Eso es", True),
        ("en", "Yeah, that's right", True),
        ("en", "Nope", False),
        ("en", "the elevator", None),
    ],
)
def test_yes_no(code: str, text: str, expected: bool | None) -> None:
    assert i18n.parser_for(code).yes_no(text) is expected


@pytest.mark.parametrize(
    ("code", "text", "refusal"),
    [
        # Spanish problems start with "no" ("no funciona", "no hay"): only a real "no," refuses
        ("es", "No funciona el portero", False),
        ("es", "No hay luz en la escalera", False),
        ("es", "No, es el 3º B", True),
        ("es", "No.", True),
        ("en", "No, it's 3B", True),
        ("en", "That's wrong", True),
        ("en", "The intercom isn't working", False),
    ],
)
def test_explicit_refusal(code: str, text: str, refusal: bool) -> None:
    assert i18n.parser_for(code).explicit_no(text) is refusal


@pytest.mark.parametrize(
    ("code", "text", "expected"),
    [
        ("es", "tercero B", (None, "3", "B")),
        ("es", "portal 2, segundo C", ("2", "2", "C")),
        ("es", "piso 1, puerta izquierda", (None, "1", "IZQ")),
        ("en", "apartment 3A", (None, "3", "A")),
        ("en", "entrance 2, third floor, B", ("2", "3", "B")),
        ("en", "unit 4 b", (None, "4", "B")),
        # The number after the apartment word is the apartment, not the floor
        ("en", "apartment 5", (None, None, "5")),
        # Devanagari ordinals end in a vowel sign: `` never matched after them
        ("hi", "तीसरी मंजिल", (None, "3", None)),
        ("hi", "तीसरी मंजिल, फ्लैट B", (None, "3", "B")),
        ("hi", "फ्लैट 5", (None, None, "5")),
        ("hi", "फ्लैट 3B", (None, "3", "B")),
        ("ar", "الطابق الثالث", (None, "3", None)),
        ("ar", "الطابق الثالث شقة 5", (None, "3", "5")),
        ("ar", "شقة 5", (None, None, "5")),
        ("ur", "تیسری منزل", (None, "3", None)),
        ("ur", "تیسری منزل، فلیٹ 5", (None, "3", "5")),
        ("ur", "فلیٹ 5", (None, None, "5")),
    ],
)
def test_apartment_by_format(code: str, text: str, expected: tuple[str | None, ...]) -> None:
    assert i18n.parser_for(code).extract_home(text) == expected


def test_spelled_out_numbers() -> None:
    assert i18n.parser_for("es").numbers_to_digits("treinta y dos") == "32"
    assert i18n.parser_for("en").numbers_to_digits("thirty two") == "32"
    assert i18n.parser_for("en").numbers_to_digits("twelve main") == "12 main"


@pytest.mark.parametrize(
    ("code", "floor", "ordinal"),
    [("en", "1", "1st"), ("en", "2", "2nd"), ("en", "3", "3rd"), ("en", "11", "11th"),
     ("en", "22", "22nd"), ("es", "3", "3º"), ("es", "atico", "atico")],
)  # fmt: skip
def test_ordinals_follow_cldr(code: str, floor: str, ordinal: str) -> None:
    assert i18n.parser_for(code).ordinal(floor) == ordinal


def test_labels_and_read_back() -> None:
    es, en = i18n.parser_for("es"), i18n.parser_for("en")
    assert es.home_label(None, "3", "B") == "3º B"
    assert es.home_label("2", "3", "B") == "portal 2 3º B"
    assert en.home_label("1", "3", "A") == "Entrance 1, Apt 3A"
    assert en.spoken_home(None, "3", "A") == "apartment 3A"
    assert es.spoken_home("2", "3", "B") == "portal 2, 3º B"


def test_lists_are_joined_in_the_language() -> None:
    assert i18n.parser_for("en").join_or(["A", "B", "C"]) == "A, B or C"
    assert i18n.parser_for("es").join_or(["A", "B"]) == "A o B"


def test_question_start() -> None:
    es, en = i18n.parser_for("es"), i18n.parser_for("en")
    assert es.question_start("Hola, buenas. ¿En qué finca?") == 14
    text = "Hi there. Which building? Tell me the street."
    assert text[en.question_start(text) or 0 :].startswith("Which building?")
    assert en.question_start("No question here.") is None


def test_stt_terms_in_each_language() -> None:
    es = i18n.parser_for("es").stt_terms(["Calle Mayor 12"], ["2"], ["3"], ["B"])
    assert {"Calle Mayor 12", "portal 2", "puerta B", "tercero B"} <= set(es)
    en = i18n.parser_for("en").stt_terms(["12 Main Street"], ["2"], ["3"], ["B"])
    assert {"12 Main Street", "entrance 2", "apartment 3B"} <= set(en)
