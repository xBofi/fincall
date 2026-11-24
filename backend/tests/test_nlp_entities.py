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
        "Llamo desde Avenida de la Alameda número 5, portal 2, tercero A. Soy Carmen."
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


def test_english_and_catalan() -> None:
    assert spacy_entities("My name is Sarah")["name"] == "Sarah"
    assert spacy_entities("Em dic Jordi")["name"] == "Jordi"
    assert spacy_entities("It's 12 Elm Street")["address"] == "12 Elm Street"


def test_a_problem_description_has_no_entities() -> None:
    assert spacy_entities("Hay una fuga de agua en el baño y está inundando el suelo") == {}
    assert spacy_entities("Soy del tercero").get("name") is None


def test_combined_keeps_the_regex_for_the_home() -> None:
    text = "portal 2, 3º B"
    assert extract_entities(text)["floor"] == regex_entities(text)["floor"] == "3"
