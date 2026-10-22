from __future__ import annotations

from building_call.graph.registry import REGISTRY, get_graph
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


async def test_initial_greeting_asks_for_estate(manager: CallManager) -> None:
    state = await manager.begin()
    assert state["current_node"] == "identify_estate"
    assert state["awaiting_user"] is True
    assert "finca" in state["bot_messages"][0].lower()


async def test_full_flow_creates_incident(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager,
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
            "sí, correcto",
        ],
    )
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert state["estate_id"] == "finca-mayor-12"
    assert state["home_id"] == "finca-mayor-12-u-3B"
    assert state["declared_name"] is None  # el nombre no se pregunta
    assert state["category"] == "plumbing"
    assert state["urgency"] == "high"
    assert state["specialist_id"] == "esp-fontanero"
    assert state["notice_channel"] == "email"  # regla por defecto para urgencia alta
    incidents = await repo.list_incidents()
    assert len(incidents) == 1 and incidents[0].state == "assigned"
    call = repo.calls[state["call_id"]]
    assert call.config_id == "cfg-mayor-12-v1"
    assert call.graph == "standard"
    assert "data_validated" in state["validations"]


async def test_estate_not_identified_after_three_attempts(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager, ["Calle Inventada 99", "Otra calle rara", "Plaza Inexistente"]
    )
    assert state["current_node"] == "estate_not_identified"
    assert state["awaiting_user"] is True
    state = await manager.message(state["call_id"], "Quería avisar de una farola rota")
    assert state["finished"] is True
    assert state["result"] == "estate_not_identified"
    assert repo.calls[state["call_id"]].result == "estate_not_identified"
    assert await repo.list_incidents() == []


async def test_rejecting_candidate_estate_counts_as_an_attempt(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "no"])
    assert state["current_node"] == "identify_estate"
    assert state["estate_attempts"] == 1
    assert state["estate_id"] is None


async def test_home_not_identified_respects_the_estate_attempts(
    manager: CallManager, repo: MemoryRepository
) -> None:
    # Alameda 5 tiene intentos_vivienda=4 en su configuración
    state = await _converse(
        manager, ["Avenida de la Alameda 5", "sí", "noveno Z", "noveno Z", "noveno Z"]
    )
    assert state["current_node"] == "identify_home"
    assert state["home_attempts"] == 3
    state = await manager.message(state["call_id"], "noveno Z")
    assert state["current_node"] == "home_not_identified"
    state = await manager.message(state["call_id"], "Se ha roto la puerta del garaje")
    assert state["result"] == "home_not_identified"
    assert state["notice_target"] == "alameda5@demo.test"
    assert await repo.list_incidents() == []


async def test_home_with_entrance(manager: CallManager) -> None:
    state = await _converse(manager, ["Alameda 5", "sí", "portal 2, segundo C"])
    assert state["home_id"] == "finca-alameda-5-2-2C"
    assert state["current_node"] == "collect_request"
    # Sin preguntar el nombre ni repetir la vivienda (ya registrada): directo al problema
    assert state["bot_messages"][-1] == "Perfecto. Cuénteme, ¿qué ocurre?"


async def test_rejecting_summary_goes_back_to_collect_request(manager: CallManager) -> None:
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "1 A", "No hay luz en la escalera", "no"]
    )
    assert state["current_node"] == "collect_request"
    assert state["confirmed"] is False
    state = await manager.message(state["call_id"], "El ascensor está parado con gente dentro")
    assert state["category"] == "elevator"
    assert state["current_node"] == "confirm_summary"


async def test_unclassifiable_creates_pending_incident(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "2 B", "He visto un ovni en la azotea", "sí"]
    )
    # Nosotros sustituimos al administrador: aunque no se clasifique, la incidencia se crea
    # y queda pendiente para él
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert "sin_categoria->pending_administrator" in state["validations"]
    assert state["specialist_id"] is None
    incidents = await repo.list_incidents()
    assert len(incidents) == 1 and incidents[0].state == "pending"
    assert "administrador la revisará" in state["bot_messages"][-1]


async def test_category_without_specialist_stays_pending_for_the_administrator(
    manager: CallManager,
) -> None:
    # "limpieza" está en el catálogo pero el administrador demo no tiene especialista asignado
    state = await _converse(
        manager,
        [
            "Calle Mayor 12",
            "sí",
            "2 B",
            "La basura del portal lleva días sin recogerse",
            "sí",
        ],
    )
    assert state["result"] == "incident_created"
    assert "no_specialist->pending_administrator" in state["validations"]
    assert state["notice_target"] == "mayor12@demo.test"


async def test_message_after_finishing_does_not_change_state(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "2 B", "He visto un ovni", "sí"])
    again = await manager.message(state["call_id"], "hola?")
    assert again == state


def test_registry_only_has_the_standard_graph(deps) -> None:
    assert sorted(REGISTRY) == ["standard"]
    assert get_graph("standard", deps) is not None


async def test_history_contains_bot_and_caller_turns(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí"])
    roles = [t["role"] for t in state["history"]]
    assert roles == ["bot", "caller", "bot", "caller", "bot"]
    assert state["history"][0]["text"].startswith("Hola")


async def test_the_summary_does_not_read_the_category(manager: CallManager) -> None:
    """La categoría es interna: el vecino oye lo que ha contado, no «fontanería» (2026-10-21)."""
    state = await _converse(
        manager,
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
        ],
    )
    summary = " ".join(state["bot_messages"])
    assert "fuga de agua" in summary
    assert "fontanería" not in summary and "fontaneria" not in summary


async def test_the_closing_keeps_the_professional_capitalisation(manager: CallManager) -> None:
    state = await _converse(
        manager,
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
            "sí, correcto",
        ],
    )
    closing = " ".join(state["bot_messages"])
    assert "pérez" not in closing and "Pérez" in closing
