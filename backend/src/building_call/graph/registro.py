"""Registro de grafos.

Cada finca referencia un grafo por nombre (`grafo` en su configuración). Hoy solo existe
`estandar`. Un grafo nuevo se registra bajo un nombre de comportamiento, nunca de finca, y solo
cuando una finca real lo necesite.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from building_call.graph.estado import EstadoLlamada
from building_call.graph.nodos import Dependencias, construir_nodos

ConstructorGrafo = Callable[[Dependencias], Any]

_ORDEN_ESTANDAR = [
    "siguiente_paso",
    "identificar_finca",
    "finca_no_identificada",
    "identificar_vivienda",
    "vivienda_no_identificada",
    "recoger_solicitud",
    "clasificar_problema",
    "pedir_detalle",
    "determinar_urgencia",
    "confirmar_resumen",
    "validar_datos",
    "no_clasificable",
    "derivar_humano",
    "despedida",
    "asignar_especialista",
    "crear_ticket",
    "avisar_canal",
]


def _siguiente(estado: EstadoLlamada) -> str:
    if estado.get("esperando_usuario") or estado.get("finalizada"):
        return END
    return estado["nodo_actual"]


def _entrada(estado: EstadoLlamada) -> str:
    return estado.get("nodo_actual") or "siguiente_paso"


def construir_estandar(deps: Dependencias) -> Any:
    """Grafo estándar: las aristas son deterministas y salen de `nodo_actual`."""
    nodos = construir_nodos(deps)
    g: StateGraph = StateGraph(EstadoLlamada)
    for nombre in _ORDEN_ESTANDAR:
        g.add_node(nombre, nodos[nombre])
    destinos = {**{n: n for n in _ORDEN_ESTANDAR}, END: END}
    g.set_conditional_entry_point(_entrada, destinos)
    for nombre in _ORDEN_ESTANDAR:
        g.add_conditional_edges(nombre, _siguiente, destinos)
    return g.compile()


REGISTRO: dict[str, ConstructorGrafo] = {
    "estandar": construir_estandar,
}


def obtener_grafo(nombre: str, deps: Dependencias) -> Any:
    try:
        return REGISTRO[nombre](deps)
    except KeyError as e:
        raise KeyError(f"Grafo no registrado: {nombre}. Disponibles: {sorted(REGISTRO)}") from e
