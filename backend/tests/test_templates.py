"""El catálogo de plantillas es lo que el vecino oye: sus contratos no son negociables.

Una plantilla mal editada no da error, degrada en silencio: si pierde el interrogante, la
redacción natural se descarta siempre; si le sobra una variable, la llamada revienta a mitad.
"""

from __future__ import annotations

import re

import pytest

from building_call.graph import templates as tpl
from building_call.graph.manifest import MANIFEST
from building_call.llm.phrasing import keeps_facts


@pytest.fixture(autouse=True)
def clean_catalog() -> object:
    """Ningún test puede dejar una sobrescritura puesta para el siguiente."""
    yield
    tpl.load({})


# --------------------------------------------------------------------- coherencia del catálogo


def test_each_template_is_declared_with_its_own_id() -> None:
    for id_, p in tpl.CATALOG.items():
        assert p.id == id_


def test_each_template_belongs_to_a_real_node() -> None:
    nodes = set(MANIFEST) | {"global"}
    for id_, p in tpl.CATALOG.items():
        assert p.node in nodes, f"{id_} dice ser del nodo {p.node}"


def test_the_declared_variables_are_those_of_the_text() -> None:
    for id_, p in tpl.CATALOG.items():
        assert set(p.variables) == set(re.findall(r"\{(\w+)\}", p.text)), id_


def test_every_question_template_asks() -> None:
    """El interrogante está en el texto, o en la variable declarada que lo aporta."""
    for id_, p in tpl.CATALOG.items():
        if not p.question:
            continue
        assert "?" in p.text or p.question_in in p.variables, id_


def test_the_variable_the_question_provides_exists_in_the_text() -> None:
    for id_, p in tpl.CATALOG.items():
        if p.question_in is not None:
            assert p.question_in in p.variables, id_


def test_no_code_template_breaks_its_own_validation() -> None:
    for id_, p in tpl.CATALOG.items():
        assert tpl.validate(id_, p.text) == [], id_


def test_every_template_explains_when_it_is_said() -> None:
    for id_, p in tpl.CATALOG.items():
        assert len(p.description.strip()) > 20, id_


def test_the_whole_catalog_is_used_from_the_nodes() -> None:
    """Una plantilla que nadie renderiza es texto muerto que alguien editará para nada."""
    import pathlib

    source = (
        pathlib.Path(tpl.__file__).parent / "nodes.py"
    ).read_text(encoding="utf-8")
    used = set(re.findall(r'render\(\s*"([\w.]+)"', source))
    assert set(tpl.CATALOG) - used == set()


# --------------------------------------------------------------------- render


def test_render_interpolates_and_remembers_its_origin() -> None:
    text = tpl.render("identify_estate.candidate", address="Calle Mayor 12")
    assert text == "He identificado Calle Mayor 12. ¿Es correcto?"
    assert text.template_id == "identify_estate.candidate"
    assert text.version == 0


def test_the_rendered_text_is_a_plain_str() -> None:
    text = tpl.render("global.filler")
    assert isinstance(text, str)
    assert text.upper().startswith("CLARO")


def test_render_does_not_touch_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """El presupuesto de latencia no admite una consulta por frase."""
    import building_call.repos.postgres as postgres

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("render ha intentado usar el repositorio")

    monkeypatch.setattr(postgres, "PostgresRepository", forbidden)
    assert tpl.render("identify_home.ask")


# --------------------------------------------------------------------- validación


def test_rejects_a_missing_variable() -> None:
    problems = tpl.validate("identify_estate.candidate", "He identificado la finca. ¿Correcto?")
    assert any("Faltan variables" in p for p in problems)


def test_rejects_an_invented_variable() -> None:
    problems = tpl.validate(
        "identify_estate.candidate", "He identificado {address} en {ciudad}. ¿Es correcto?"
    )
    assert any("no rellena" in p for p in problems)


def test_rejects_a_question_without_question_mark() -> None:
    problems = tpl.validate("identify_estate.candidate", "He identificado {address}.")
    assert any("terminar preguntando" in p for p in problems)


def test_rejects_line_breaks() -> None:
    problems = tpl.validate("global.filler", "Claro.\nDígame.")
    assert any("saltos de línea" in p for p in problems)


def test_rejects_an_overlong_text() -> None:
    problems = tpl.validate("global.filler", "Claro. " * 100)
    assert any("Demasiado largo" in p for p in problems)


def test_rejects_empty_text() -> None:
    assert tpl.validate("global.filler", "   ") == ["El texto no puede estar vacío."]


def test_accepts_a_legitimate_rewrite() -> None:
    assert tpl.validate("identify_estate.candidate", "¿Me confirma que es {address}?") == []


# --------------------------------------------------------------------- publicar y restaurar


def test_publishing_changes_what_the_bot_says() -> None:
    tpl.apply("identify_estate.candidate", "¿Me confirma que es {address}?", version=1)
    text = tpl.render("identify_estate.candidate", address="Calle Mayor 12")
    assert text == "¿Me confirma que es Calle Mayor 12?"
    assert text.version == 1
    assert tpl.is_overridden("identify_estate.candidate")


def test_restore_goes_back_to_the_code_text() -> None:
    tpl.apply("global.filler", "Sin problema, tómese su tiempo.", version=3)
    tpl.restore("global.filler")
    assert tpl.effective_text("global.filler") == tpl.CATALOG["global.filler"].text
    assert not tpl.is_overridden("global.filler")


def test_publishing_something_invalid_is_never_applied() -> None:
    original = tpl.effective_text("identify_estate.candidate")
    with pytest.raises(tpl.InvalidTemplate):
        tpl.apply("identify_estate.candidate", "He identificado la finca.", version=1)
    assert tpl.effective_text("identify_estate.candidate") == original


def test_load_skips_ids_that_no_longer_exist() -> None:
    tpl.load({"node.what.ya.no.existingIds": ("hola", 1), "global.filler": ("Un momento.", 2)})
    assert tpl.effective_text("global.filler") == "Un momento."


# --------------------------------------------------------------------- contrato con la redacción


def test_an_edited_template_still_allows_paraphrase() -> None:
    """Si la redacción no puede conservar los hechos, el vecino oye siempre el texto plano."""
    tpl.apply("identify_estate.candidate", "¿Me confirma que es {address}?", version=1)
    template = tpl.render("identify_estate.candidate", address="Calle Mayor 12")
    paraphrase = "Entendido. ¿Me confirma que su finca es Calle Mayor 12?"
    assert keeps_facts(template, paraphrase)
