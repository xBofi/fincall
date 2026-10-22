"""Registro de grafos.

Cada finca referencia un grafo por nombre (`graph` en su configuración). Hoy solo existe
`standard`. Un grafo nuevo se registra bajo un nombre de comportamiento, nunca de finca, y solo
cuando una finca real lo necesite.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from building_call.graph.nodes import Dependencies, build_nodes
from building_call.graph.state import CallState

GraphBuilder = Callable[[Dependencies], Any]

_STANDARD_ORDER = [
    "next_step",
    "identify_estate",
    "estate_not_identified",
    "identify_home",
    "home_not_identified",
    "collect_request",
    "classify_problem",
    "ask_detail",
    "determine_urgency",
    "handle_emergency",
    "confirm_summary",
    "validate_data",
    "unclassifiable",
    "handoff_to_human",
    "farewell",
    "assign_specialist",
    "create_ticket",
    "notify_channel",
]


def _next(state: CallState) -> str:
    if state.get("awaiting_user") or state.get("finished"):
        return END
    return state["current_node"]


def _utterance(state: CallState) -> str:
    return state.get("current_node") or "next_step"


def build_standard(deps: Dependencies) -> Any:
    """Grafo estándar: las aristas son deterministas y salen de `current_node`."""
    nodes = build_nodes(deps)
    # Los genéricos de StateGraph cambian entre versiones de LangGraph: no aportan aquí
    g: Any = StateGraph(CallState)
    for name in _STANDARD_ORDER:
        g.add_node(name, nodes[name])
    targets = {**{n: n for n in _STANDARD_ORDER}, END: END}
    g.set_conditional_entry_point(_utterance, targets)
    for name in _STANDARD_ORDER:
        g.add_conditional_edges(name, _next, targets)
    return g.compile()


REGISTRY: dict[str, GraphBuilder] = {
    "standard": build_standard,
}


def get_graph(name: str, deps: Dependencies) -> Any:
    try:
        return REGISTRY[name](deps)
    except KeyError as e:
        raise KeyError(f"Grafo no registrado: {name}. Disponibles: {sorted(REGISTRY)}") from e
