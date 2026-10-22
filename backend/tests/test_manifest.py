"""El manifiesto del grafo tiene que describir el grafo de verdad, no el que nos gustaría.

`registry.py` conecta todos los nodos con todos, así que nada en LangGraph impide una transición
inventada. Estos tests, junto con la verificación que `nodes.py` hace en cada salto, son lo único
que mantiene el manifiesto honesto: el resto de la suite recorre el flujo y cualquier arista no
declarada revienta, porque bajo pytest `estricto()` es cierto.
"""

from __future__ import annotations

import pytest

from building_call.graph.manifest import (
    FINISH,
    MANIFEST,
    TRANSITIONS,
    UndeclaredTransition,
    verify_transition,
)
from building_call.graph.registry import _STANDARD_ORDER


def test_manifest_covers_exactly_the_graph_nodes() -> None:
    assert set(MANIFEST) == set(_STANDARD_ORDER)


def test_each_node_is_declared_with_its_own_id() -> None:
    for id_, node in MANIFEST.items():
        assert node.id == id_


def test_every_target_is_a_known_node_or_the_end() -> None:
    known = set(_STANDARD_ORDER) | {FINISH}
    for id_, node in MANIFEST.items():
        for edge in node.targets:
            assert edge.target in known, f"{id_} -> {edge.target}"


def test_no_node_is_left_without_exit() -> None:
    for id_, node in MANIFEST.items():
        assert node.targets, f"{id_} no declara ningún destino"


def test_every_edge_explains_its_condition() -> None:
    for id_, node in MANIFEST.items():
        for edge in node.targets:
            assert edge.condition.strip(), f"{id_} -> {edge.target} sin condición"


def test_every_node_is_described_in_plain_spanish() -> None:
    for id_, node in MANIFEST.items():
        assert node.title.strip(), id_
        assert len(node.summary.strip()) > 40, f"{id_}: el resumen no explica nada"


def test_all_nodes_are_reachable_from_the_entry() -> None:
    reached: set[str] = set()
    pending = ["next_step"]
    while pending:
        current = pending.pop()
        if current in reached or current == FINISH:
            continue
        reached.add(current)
        pending.extend(TRANSITIONS[current])
    assert set(MANIFEST) - reached == set()


def test_a_declared_transition_passes() -> None:
    assert verify_transition("create_ticket", "notify_channel") is None


def test_an_invented_transition_blows_up_under_pytest() -> None:
    with pytest.raises(UndeclaredTransition):
        verify_transition("create_ticket", "farewell")


def test_an_unknown_source_does_not_block() -> None:
    # El primer salto de la llamada no tiene nodo de origen todavía
    assert verify_transition(None, "next_step") is None


def test_effect_nodes_declare_what_they_touch() -> None:
    for id_, node in MANIFEST.items():
        if node.kind in ("effect", "terminal"):
            assert node.effects, f"{id_} es de tipo {node.kind} y no declara efectos"


def test_question_nodes_accept_global_intents() -> None:
    """Todo nodo que consume un turno puede acabar derivando o despidiéndose."""
    for id_, node in MANIFEST.items():
        if node.kind != "question":
            continue
        targets = TRANSITIONS[id_]
        assert "handoff_to_human" in targets, id_
        assert "farewell" in targets, id_


def test_the_stages_order_the_conversation() -> None:
    """La etapa solo dibuja, pero si miente el grafo se lee al revés de como ocurre."""
    stages = {id_: n.stage for id_, n in MANIFEST.items()}
    assert stages["next_step"] == 0, "el enrutador es el origen de todo"
    assert stages["identify_estate"] < stages["identify_home"]
    assert stages["identify_home"] < stages["collect_request"]
    assert "record_name" not in stages, "el nombre no se pregunta: basta la vivienda"
    assert stages["collect_request"] < stages["confirm_summary"]
    assert stages["confirm_summary"] < stages["validate_data"]
    assert stages["validate_data"] < stages["create_ticket"]
