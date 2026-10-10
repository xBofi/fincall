"""El catálogo de plantillas es lo que el vecino oye: sus contratos no son negociables.

Una plantilla mal editada no da error, degrada en silencio: si pierde el interrogante, la
redacción natural se descarta siempre; si le sobra una variable, la llamada revienta a mitad.
"""

from __future__ import annotations

import re

import pytest

from building_call.graph import plantillas as pl
from building_call.graph.manifiesto import MANIFIESTO
from building_call.llm.redaccion import conserva_hechos


@pytest.fixture(autouse=True)
def catalogo_limpio() -> object:
    """Ningún test puede dejar una sobrescritura puesta para el siguiente."""
    yield
    pl.cargar({})


# --------------------------------------------------------------------- coherencia del catálogo


def test_cada_plantilla_se_declara_con_su_propio_id() -> None:
    for id_, p in pl.CATALOGO.items():
        assert p.id == id_


def test_cada_plantilla_pertenece_a_un_nodo_real() -> None:
    nodos = set(MANIFIESTO) | {"global"}
    for id_, p in pl.CATALOGO.items():
        assert p.nodo in nodos, f"{id_} dice ser del nodo {p.nodo}"


def test_las_variables_declaradas_son_las_del_texto() -> None:
    for id_, p in pl.CATALOGO.items():
        assert set(p.variables) == set(re.findall(r"\{(\w+)\}", p.texto)), id_


def test_toda_plantilla_de_pregunta_pregunta() -> None:
    """El interrogante está en el texto, o en la variable declarada que lo aporta."""
    for id_, p in pl.CATALOGO.items():
        if not p.pregunta:
            continue
        assert "?" in p.texto or p.pregunta_en in p.variables, id_


def test_la_variable_que_aporta_la_pregunta_existe_en_el_texto() -> None:
    for id_, p in pl.CATALOGO.items():
        if p.pregunta_en is not None:
            assert p.pregunta_en in p.variables, id_


def test_ninguna_plantilla_de_codigo_incumple_su_propia_validacion() -> None:
    for id_, p in pl.CATALOGO.items():
        assert pl.validar(id_, p.texto) == [], id_


def test_toda_plantilla_explica_cuando_se_dice() -> None:
    for id_, p in pl.CATALOGO.items():
        assert len(p.descripcion.strip()) > 20, id_


def test_el_catalogo_se_usa_entero_desde_los_nodos() -> None:
    """Una plantilla que nadie renderiza es texto muerto que alguien editará para nada."""
    import pathlib

    fuente = (
        pathlib.Path(pl.__file__).parent / "nodos.py"
    ).read_text(encoding="utf-8")
    usadas = set(re.findall(r'render\(\s*"([\w.]+)"', fuente))
    assert set(pl.CATALOGO) - usadas == set()


# --------------------------------------------------------------------- render


def test_render_interpola_y_recuerda_su_origen() -> None:
    texto = pl.render("identificar_finca.candidata", direccion="Calle Mayor 12")
    assert texto == "He identificado Calle Mayor 12. ¿Es correcto?"
    assert texto.plantilla_id == "identificar_finca.candidata"
    assert texto.version == 0


def test_el_texto_renderizado_es_un_str_normal() -> None:
    texto = pl.render("global.relleno")
    assert isinstance(texto, str)
    assert texto.upper().startswith("CLARO")


def test_render_no_toca_la_base_de_datos(monkeypatch: pytest.MonkeyPatch) -> None:
    """El presupuesto de latencia no admite una consulta por frase."""
    import building_call.repos.postgres as postgres

    def prohibido(*_: object, **__: object) -> None:
        raise AssertionError("render ha intentado usar el repositorio")

    monkeypatch.setattr(postgres, "RepositorioPostgres", prohibido)
    assert pl.render("identificar_vivienda.preguntar")


# --------------------------------------------------------------------- validación


def test_rechaza_una_variable_que_falta() -> None:
    problemas = pl.validar("identificar_finca.candidata", "He identificado la finca. ¿Correcto?")
    assert any("Faltan variables" in p for p in problemas)


def test_rechaza_una_variable_inventada() -> None:
    problemas = pl.validar(
        "identificar_finca.candidata", "He identificado {direccion} en {ciudad}. ¿Es correcto?"
    )
    assert any("no rellena" in p for p in problemas)


def test_rechaza_una_pregunta_sin_interrogante() -> None:
    problemas = pl.validar("identificar_finca.candidata", "He identificado {direccion}.")
    assert any("terminar preguntando" in p for p in problemas)


def test_rechaza_saltos_de_linea() -> None:
    problemas = pl.validar("global.relleno", "Claro.\nDígame.")
    assert any("saltos de línea" in p for p in problemas)


def test_rechaza_un_texto_kilometrico() -> None:
    problemas = pl.validar("global.relleno", "Claro. " * 100)
    assert any("Demasiado largo" in p for p in problemas)


def test_rechaza_el_texto_vacio() -> None:
    assert pl.validar("global.relleno", "   ") == ["El texto no puede estar vacío."]


def test_acepta_una_reescritura_legitima() -> None:
    assert pl.validar("identificar_finca.candidata", "¿Me confirma que es {direccion}?") == []


# --------------------------------------------------------------------- publicar y restaurar


def test_publicar_cambia_lo_que_dice_el_bot() -> None:
    pl.aplicar("identificar_finca.candidata", "¿Me confirma que es {direccion}?", version=1)
    texto = pl.render("identificar_finca.candidata", direccion="Calle Mayor 12")
    assert texto == "¿Me confirma que es Calle Mayor 12?"
    assert texto.version == 1
    assert pl.esta_sobrescrita("identificar_finca.candidata")


def test_restaurar_vuelve_al_texto_del_codigo() -> None:
    pl.aplicar("global.relleno", "Sin problema, tómese su tiempo.", version=3)
    pl.restaurar("global.relleno")
    assert pl.texto_efectivo("global.relleno") == pl.CATALOGO["global.relleno"].texto
    assert not pl.esta_sobrescrita("global.relleno")


def test_publicar_algo_invalido_no_llega_a_aplicarse() -> None:
    original = pl.texto_efectivo("identificar_finca.candidata")
    with pytest.raises(pl.PlantillaInvalida):
        pl.aplicar("identificar_finca.candidata", "He identificado la finca.", version=1)
    assert pl.texto_efectivo("identificar_finca.candidata") == original


def test_cargar_deja_fuera_ids_que_ya_no_existen() -> None:
    pl.cargar({"nodo.que.ya.no.existe": ("hola", 1), "global.relleno": ("Un momento.", 2)})
    assert pl.texto_efectivo("global.relleno") == "Un momento."


# --------------------------------------------------------------------- contrato con la redacción


def test_una_plantilla_editada_sigue_permitiendo_la_parafrasis() -> None:
    """Si la redacción no puede conservar los hechos, el vecino oye siempre el texto plano."""
    pl.aplicar("identificar_finca.candidata", "¿Me confirma que es {direccion}?", version=1)
    plantilla = pl.render("identificar_finca.candidata", direccion="Calle Mayor 12")
    parafrasis = "Entendido. ¿Me confirma que su finca es Calle Mayor 12?"
    assert conserva_hechos(plantilla, parafrasis)
