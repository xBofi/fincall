"""Estructura del grafo para la vista de enrutado, sacada del grafo compilado de LangGraph.

Los nodos salen de `compiled.get_graph()`: si alguien registra o quita un nodo en
`registry.py`, la vista cambia sin tocar el frontend. Las aristas compiladas, en cambio, no
dicen nada útil: `registry.py` conecta todos los nodos con todos mediante un enrutador
condicional (`_next`) que lee `current_node`. Por eso las rutas con su condición salen del
manifiesto (`manifest.py`), que se verifica en cada transición, y aquí se comprueba además que
cada ruta declarada existe como arista condicional en el grafo compilado. Lo que no case se
devuelve en `drift`, y los tests exigen que esté vacío.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from langgraph.graph import END, START

from building_call.graph.manifest import FINISH, MANIFEST, ManifestNode
from building_call.graph.nodes import Dependencies
from building_call.graph.registry import REGISTRY, ROUTERS

#: Tipo de nodo en la vista. Es presentación derivada del manifiesto, no lógica.
Role = Literal["entry", "router", "llm", "tool", "human", "end"]

ENTRY_ID = START
END_ID = END


def role_of(node: ManifestNode) -> Role:
    """Tipo visible: quien deriva a una persona, quien llama al modelo, quien tiene efectos..."""
    if node.id == "handoff_to_human":
        return "human"
    if node.kind == "terminal":
        return "end"
    if node.uses_llm:
        return "llm"
    if node.kind == "effect":
        return "tool"
    return "router"


@lru_cache(maxsize=8)
def _compiled_structure(name: str) -> tuple[frozenset[str], frozenset[tuple[str, str, bool]]]:
    """Nodos y aristas del grafo compilado. Los nodos no se ejecutan: sin dependencias reales."""
    stub: Any = Dependencies(repo=None, llm=None)  # type: ignore[arg-type]
    drawable = REGISTRY[name](stub).get_graph()
    nodes = frozenset(drawable.nodes)
    edges = frozenset((e.source, e.target, bool(e.conditional)) for e in drawable.edges)
    return nodes, edges


def export_graph(name: str = "standard") -> dict[str, Any]:
    """Nodos, rutas condicionales con su motivo y la comprobación contra el grafo compilado."""
    compiled_nodes, compiled_edges = _compiled_structure(name)
    compiled_pairs = {(s, t) for s, t, _ in compiled_edges}
    conditional_pairs = {(s, t) for s, t, c in compiled_edges if c}
    business_nodes = [n for n in compiled_nodes if n not in (START, END)]

    drift: list[str] = []
    for node_id in sorted(business_nodes):
        if node_id not in MANIFEST:
            drift.append(f"{node_id}: compilado pero sin declarar en el manifiesto")
    for node_id in MANIFEST:
        if node_id not in compiled_nodes:
            drift.append(f"{node_id}: declarado en el manifiesto pero no compilado")

    # Orden de la vista: el del manifiesto (por etapas), y los no declarados al final
    ordered = [n for n in MANIFEST if n in compiled_nodes] + sorted(
        n for n in business_nodes if n not in MANIFEST
    )
    nodes: list[dict[str, Any]] = [
        {
            "id": ENTRY_ID,
            "role": "entry",
            "title": "Turno del vecino",
            "summary": (
                "Cada turno es una invocación del grafo. El punto de entrada condicional "
                f"(`{ROUTERS['entry']}`) retoma la llamada en el nodo donde se quedó."
            ),
            "stage": -1,
            "uses_llm": False,
        }
    ]
    for node_id in ordered:
        m = MANIFEST.get(node_id)
        nodes.append(
            {
                "id": node_id,
                "role": role_of(m) if m else "router",
                "kind": m.kind if m else None,
                "title": m.title if m else node_id,
                "summary": m.summary if m else "",
                "stage": m.stage if m else 99,
                "uses_llm": m.uses_llm if m else False,
                "deterministic_paths": list(m.deterministic_paths) if m else [],
                "effects": list(m.effects) if m else [],
            }
        )
    nodes.append(
        {
            "id": END_ID,
            "role": "end",
            "title": "Fin del turno o de la llamada",
            "summary": (
                "El grafo se detiene cuando un nodo espera la respuesta del vecino o cuando la "
                "llamada termina."
            ),
            "stage": 100,
            "uses_llm": False,
        }
    )

    edges: list[dict[str, Any]] = []
    for node_id in ordered:
        m = MANIFEST.get(node_id)
        if m is None:
            continue
        for edge in m.targets:
            target = END_ID if edge.target == FINISH else edge.target
            in_compiled = (node_id, target) in compiled_pairs
            if not in_compiled:
                drift.append(f"{node_id} -> {edge.target}: declarada pero no compilada")
            edges.append(
                {
                    "id": f"{node_id}->{target}",
                    "source": node_id,
                    "target": target,
                    "condition": edge.condition,
                    "conditional": (node_id, target) in conditional_pairs,
                    "router": ROUTERS["edges"],
                    "kind": _edge_kind(node_id, target),
                }
            )
    entry_targets = sorted(t for s, t, _ in compiled_edges if s == START and t != END)
    return {
        "graph": name,
        "entry": ENTRY_ID,
        "end": END_ID,
        "hub": "next_step",
        "routers": dict(ROUTERS),
        "nodes": nodes,
        "edges": edges,
        # El punto de entrada puede retomar en cualquier nodo: no se dibuja como aristas
        "entry_targets": entry_targets,
        "compiled": {
            "nodes": len(compiled_nodes),
            "edges": len(compiled_edges),
            "conditional_edges": len(conditional_pairs),
        },
        "drift": drift,
    }


def _edge_kind(source: str, target: str) -> str:
    """Pista de dibujo: vuelta al reparto, repregunta, salida global o avance."""
    if source == target:
        return "retry"
    if target == "next_step":
        return "return"
    if target in ("handoff_to_human", "farewell") and source != "next_step":
        return "global"
    return "forward"
