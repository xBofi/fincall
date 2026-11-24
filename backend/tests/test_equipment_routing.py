"""A quién llega cada incidencia: mantenedor del equipo, especialista o administrador.

Regla de producto (2026-10-21, Provisional): solo se avisa directamente a un profesional cuando es
una reparación, hay alguien para ella y el vecino confirma sin objeciones. Lo demás lo decide el
administrador, con una sugerencia. Spec: 2026-10-21-equipment-inventory-and-routing-design.md.
"""

from __future__ import annotations

from typing import Any

from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager

START = ["Calle Mayor 12", "sí", "1 A"]


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


async def _incident(repo: MemoryRepository) -> Any:
    incidents = await repo.list_incidents()
    assert len(incidents) == 1
    return incidents[0]


async def test_the_summary_reads_neither_category_nor_urgency(manager: CallManager) -> None:
    state = await _converse(manager, [*START, "Gotea el grifo del patio"])
    summary = state["bot_messages"][-1]
    assert summary.startswith("Le resumo")
    assert "clasificado" not in summary and "urgencia" not in summary


async def test_a_leak_goes_to_the_plumber_as_always(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, [*START, "Gotea el grifo del patio", "sí"])
    inc = await _incident(repo)
    assert inc.specialist_id == "esp-fontanero" and inc.work_type == "repair"
    assert "Fontanería Pérez" in state["bot_messages"][-1]


async def test_equipment_fault_goes_to_its_maintainer(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, [*START, "El portero automático no funciona", "sí"])
    inc = await _incident(repo)
    assert inc.equipment_id == "eq-mayor12-portero"
    assert inc.specialist_id == "esp-porteros"
    assert "Fermax" in (inc.suggestion or "") and "PL-2024-118" in (inc.suggestion or "")
    assert "Porteros Levante" in state["bot_messages"][-1]


async def test_vandalism_goes_to_the_administrator_with_suggestion(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager, [*START, "Han vandalizado el portero automático, lo han reventado", "sí"]
    )
    inc = await _incident(repo)
    assert inc.specialist_id is None and inc.state == "pending"
    assert inc.work_type == "insurance_claim" and inc.equipment_id == "eq-mayor12-portero"
    assert "seguro" in (inc.suggestion or "") and "Porteros Levante" in (inc.suggestion or "")
    closing = state["bot_messages"][-1]
    assert "administrador" in closing and "Porteros Levante" not in closing


async def test_yes_but_with_objection_goes_to_the_administrator(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager,
        [*START, "No funciona la luz del portal", "Sí, pero eso no lo arregla un electricista"],
    )
    assert state["result"] == "incident_created"
    inc = await _incident(repo)
    assert inc.specialist_id is None
    assert "no lo arregla un electricista" in (inc.suggestion or "")
    assert "Electricidad Gómez" in (inc.suggestion or "")  # lo que habría hecho el bot


async def test_the_equipment_is_recognised_even_if_the_fault_is_told_before_the_estate(
    manager: CallManager, repo: MemoryRepository
) -> None:
    await _converse(
        manager, ["Calle Mayor 12, el portero automático no funciona", "sí", "1 A", "sí", "sí"]
    )
    inc = await _incident(repo)
    assert inc.equipment_id == "eq-mayor12-portero" and inc.specialist_id == "esp-porteros"


async def test_the_detail_asks_about_the_equipment_not_the_category(
    detail_manager: CallManager,
) -> None:
    state = await _converse(detail_manager, [*START, "El portero automático no funciona"])
    assert state["current_node"] == "ask_detail"
    question = state["bot_messages"][-1]
    assert "portero automático" in question and "electricista" not in question


async def test_an_insurance_claim_does_not_ask_detail(detail_manager: CallManager) -> None:
    state = await _converse(
        detail_manager, [*START, "Han vandalizado el portero automático, lo han reventado"]
    )
    assert state["current_node"] == "confirm_summary"
