"""El manifiesto del grafo tiene que describir el grafo de verdad, no el que nos gustaría.

`registro.py` conecta todos los nodos con todos, así que nada en LangGraph impide una transición
inventada. Estos tests, junto con la verificación que `nodos.py` hace en cada salto, son lo único
que mantiene el manifiesto honesto: el resto de la suite recorre el flujo y cualquier arista no
declarada revienta, porque bajo pytest `estricto()` es cierto.
"""

from __future__ import annotations

import pytest

from building_call.graph.manifiesto import (
    FIN,
    MANIFIESTO,
    TRANSICIONES,
    TransicionNoDeclarada,
    verificar_transicion,
)
from building_call.graph.registro import _ORDEN_ESTANDAR


def test_manifiesto_cubre_exactamente_los_nodos_del_grafo() -> None:
    assert set(MANIFIESTO) == set(_ORDEN_ESTANDAR)


def test_cada_nodo_se_declara_con_su_propio_id() -> None:
    for id_, nodo in MANIFIESTO.items():
        assert nodo.id == id_


def test_todo_destino_es_un_nodo_conocido_o_el_fin() -> None:
    conocidos = set(_ORDEN_ESTANDAR) | {FIN}
    for id_, nodo in MANIFIESTO.items():
        for arista in nodo.destinos:
            assert arista.destino in conocidos, f"{id_} -> {arista.destino}"


def test_ningun_nodo_se_queda_sin_salida() -> None:
    for id_, nodo in MANIFIESTO.items():
        assert nodo.destinos, f"{id_} no declara ningún destino"


def test_toda_arista_explica_su_condicion() -> None:
    for id_, nodo in MANIFIESTO.items():
        for arista in nodo.destinos:
            assert arista.condicion.strip(), f"{id_} -> {arista.destino} sin condición"


def test_todo_nodo_se_describe_en_espanol_llano() -> None:
    for id_, nodo in MANIFIESTO.items():
        assert nodo.titulo.strip(), id_
        assert len(nodo.resumen.strip()) > 40, f"{id_}: el resumen no explica nada"


def test_todos_los_nodos_son_alcanzables_desde_la_entrada() -> None:
    alcanzados: set[str] = set()
    pendientes = ["siguiente_paso"]
    while pendientes:
        actual = pendientes.pop()
        if actual in alcanzados or actual == FIN:
            continue
        alcanzados.add(actual)
        pendientes.extend(TRANSICIONES[actual])
    assert set(MANIFIESTO) - alcanzados == set()


def test_una_transicion_declarada_pasa() -> None:
    assert verificar_transicion("crear_ticket", "avisar_canal") is None


def test_una_transicion_inventada_revienta_bajo_pytest() -> None:
    with pytest.raises(TransicionNoDeclarada):
        verificar_transicion("crear_ticket", "despedida")


def test_un_origen_desconocido_no_bloquea() -> None:
    # El primer salto de la llamada no tiene nodo de origen todavía
    assert verificar_transicion(None, "siguiente_paso") is None


def test_los_nodos_de_efecto_declaran_lo_que_tocan() -> None:
    for id_, nodo in MANIFIESTO.items():
        if nodo.tipo in ("efecto", "terminal"):
            assert nodo.efectos, f"{id_} es de tipo {nodo.tipo} y no declara efectos"


def test_los_nodos_que_preguntan_admiten_intenciones_globales() -> None:
    """Todo nodo que consume un turno puede acabar derivando o despidiéndose."""
    for id_, nodo in MANIFIESTO.items():
        if nodo.tipo != "pregunta":
            continue
        destinos = TRANSICIONES[id_]
        assert "derivar_humano" in destinos, id_
        assert "despedida" in destinos, id_


def test_las_etapas_ordenan_la_conversacion() -> None:
    """La etapa solo dibuja, pero si miente el grafo se lee al revés de como ocurre."""
    etapas = {id_: n.etapa for id_, n in MANIFIESTO.items()}
    assert etapas["siguiente_paso"] == 0, "el enrutador es el origen de todo"
    assert etapas["identificar_finca"] < etapas["identificar_vivienda"]
    assert etapas["identificar_vivienda"] < etapas["registrar_nombre"]
    assert etapas["registrar_nombre"] < etapas["recoger_solicitud"]
    assert etapas["recoger_solicitud"] < etapas["confirmar_resumen"]
    assert etapas["confirmar_resumen"] < etapas["validar_datos"]
    assert etapas["validar_datos"] < etapas["crear_ticket"]
