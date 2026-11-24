"""spaCy EntityRuler entities, combined with the graph's regex. Skipped without the nlp extra."""

from __future__ import annotations

import pytest

pytest.importorskip("spacy")

from building_call.nlp.entities import (  # noqa: E402
    extract_entities,
    regex_entities,
    spacy_entities,
)


def test_address_entrance_floor_door_and_name() -> None:
    found = spacy_entities(
        "Llamo desde Avenida de la Alameda número 5, portal 2, tercero A. Soy Carmen.", "es"
    )
    assert found["address"].startswith("Avenida de la Alameda")
    assert found["entrance"] == "2"
    assert found["floor"] == "3" and found["door"] == "A"
    assert found["name"] == "Carmen"


def test_short_home_forms() -> None:
    assert spacy_entities("3º B") == {"floor": "3", "door": "B"}
    assert spacy_entities("el bajo, puerta 2") == {"floor": "0", "door": "2"}


def test_contact_details_and_building() -> None:
    found = spacy_entities(
        "Soy Luis, bloque B, mi teléfono es 600 123 456 y mi correo luis@example.com"
    )
    assert found["name"] == "Luis"
    assert found["building"] == "B"
    assert found["phone"] == "600123456"
    assert found["email"] == "luis@example.com"


def test_the_active_language_is_the_default() -> None:
    from building_call import i18n

    with i18n.use("en"):
        assert spacy_entities("My name is Sarah")["name"] == "Sarah"


def test_english() -> None:
    assert spacy_entities("My name is Sarah", "en")["name"] == "Sarah"
    assert spacy_entities("Third floor, B, I'm Marta Lopez", "en")["name"] == "Marta Lopez"
    assert spacy_entities("It's 12 Elm Street", "en")["address"] == "12 Elm Street"
    assert spacy_entities("Main Street, number twelve", "en")["address"].startswith("Main Street")


#: One turn per language with every field: (text, expected)
ALL_FIELDS = {
    "es": ("Soy Carmen, calle Mayor 12, portal 2, tercero B", "calle Mayor 12", "Carmen", "B"),
    "en": ("My name is Sarah, 12 Elm Street, entrance 2, third floor, apartment B",
           "12 Elm Street", "Sarah", "B"),
    "ca": ("Em dic Jordi, carrer Gran 10, escala 2, tercer pis, porta B", "carrer Gran 10",
           "Jordi", "B"),
    "ar": ("اسمي أحمد، شارع النيل رقم 12، المدخل 2، الطابق الثالث، شقة 5", "شارع النيل رقم 12",
           "أحمد", "5"),
    "hi": ("मेरा नाम राहुल है, एमजी रोड 12, ब्लॉक 2, तीसरी मंजिल, फ्लैट 5", "एमजी रोड 12", "राहुल", "5"),
    "ur": ("میرا نام احمد ہے، مال روڈ 12، بلاک 2، تیسری منزل، فلیٹ 5", "مال روڈ 12", "احمد", "5"),
}  # fmt: skip


@pytest.mark.parametrize("language", sorted(ALL_FIELDS))
def test_every_language(language: str) -> None:
    text, address, name, door = ALL_FIELDS[language]
    found = spacy_entities(text, language)
    assert found == {
        "name": name, "address": address, "entrance": "2", "floor": "3", "door": door,
    }  # fmt: skip


@pytest.mark.parametrize("language", sorted(ALL_FIELDS))
def test_combined_in_every_language(language: str) -> None:
    text = ALL_FIELDS[language][0]
    combined = extract_entities(text, language)
    assert combined["name"] == ALL_FIELDS[language][2]
    assert combined["floor"] == "3"


def test_every_registered_language_has_its_row() -> None:
    from building_call import i18n
    from building_call.nlp.entities import _EXTRA

    assert set(i18n.languages()) <= set(_EXTRA) | {"xx"}
    assert set(ALL_FIELDS) == set(i18n.languages())


def test_a_problem_description_has_no_entities() -> None:
    assert spacy_entities("Hay una fuga de agua en el baño y está inundando el suelo") == {}
    assert spacy_entities("Soy del tercero").get("name") is None


def test_combined_keeps_the_regex_for_the_home() -> None:
    text = "portal 2, 3º B"
    assert extract_entities(text)["floor"] == regex_entities(text)["floor"] == "3"
