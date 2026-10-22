"""Tratamiento especial de emergencias (gas, fuego, humo, alguien atrapado).

Bóveda (Confirmado por Andreu): las emergencias están desactivadas inicialmente y se tratan como
`high`. Con el ajuste `emergencies_enabled`, el bot da primero una indicación de seguridad, no
pide detalle ni lee el resumen largo, crea la incidencia como emergencia y deja el aviso al
coordinador y al teléfono oficial de la finca como simulado (nunca llama ni envía nada).
"""

from __future__ import annotations

import pytest

from building_call.graph.nodes import Dependencies, _safety_instruction
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager, _deserves_phrasing

START = ["Calle Mayor 12", "sí", "tercero B"]


def _activate(repo: MemoryRepository, **others: object) -> None:
    repo.administrator_config["adm-demo"] = repo.administrator_config["adm-demo"].model_copy(
        update={"emergencies_enabled": True, **others}
    )


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


def _templates(state: dict) -> list[str]:
    return [t["detail"].split(" ")[0] for t in state["trace"] if t["action"] == "template"]


# --------------------------------------------------------------------- desactivado (por defecto)


async def test_disabled_the_emergency_follows_the_high_urgency_flow(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, [*START, "Huele a gas en el rellano del tercero"])
    assert state["declared_urgency"] == "emergency"
    assert state["urgency"] == "high"
    assert state["current_node"] == "confirm_summary"
    said = " ".join(state["bot_messages"])
    assert "112" not in said
    state = await manager.message(state["call_id"], "sí")
    assert state["result"] == "incident_created"
    assert state["notice_channel"] == "email"  # regla de `high`
    [incident] = await repo.list_incidents()
    assert incident.urgency == "high"
    assert not any(t["node"] == "handle_emergency" for t in state["trace"])


# --------------------------------------------------------------------- activado


async def test_enabled_gas_gives_safety_instruction_and_confirms_the_minimum(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, "Huele a gas en el rellano del tercero"])
    assert state["urgency"] == "emergency"
    assert state["current_node"] == "handle_emergency"
    assert state["awaiting_user"] is True
    message = state["bot_messages"][-1]
    assert "112" in message
    assert "no encienda luces" in message.lower()
    assert "Calle Mayor 12" in message
    assert message.rstrip().endswith("?")
    # Ni resumen largo ni pregunta de detalle
    assert "Le resumo" not in message
    assert "handle_emergency.notice" in _templates(state)
    assert "handle_emergency.gas_safety" in _templates(state)
    # La indicación de seguridad es un aviso: si el vecino la corta, se vuelve a decir
    assert any("112" in a for a in state["turn_notices"])


async def test_enabled_confirmed_creates_emergency_incident_with_simulated_notice(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, "Huele a gas en el rellano del tercero", "sí"])
    assert state["finished"] is True
    assert state["result"] == "incident_created"
    assert state["confirmed"] is True
    [incident] = await repo.list_incidents()
    assert incident.urgency == "emergency"
    assert incident.suggestion and "emergencia" in incident.suggestion.lower()
    assert "simulad" in incident.suggestion.lower()
    # Aviso por llamada al teléfono oficial de la finca: simulado, nunca real
    assert state["notice_channel"] == "call"
    assert state["notice_target"] == "+34910000001"
    notices = [t for t in state["trace"] if t["action"] == "simulated_emergency_notice"]
    assert len(notices) == 1
    assert "+34910000001" in notices[0]["detail"]
    assert "mayor12@demo.test" in notices[0]["detail"]  # coordinador
    closing = state["bot_messages"][-1]
    assert "112" in closing
    assert "notify_channel.emergency_close" in _templates(state)
    assert "confirm_summary" not in {t["node"] for t in state["trace"]}


async def test_enabled_does_not_ask_detail_even_if_the_estate_has_it_on(
    llm, repo: MemoryRepository
) -> None:
    _activate(repo, ask_detail=True)
    manager = CallManager(Dependencies(repo=repo, llm=llm))
    state = await _converse(manager, [*START, "Huele a gas"])
    assert state["current_node"] == "handle_emergency"
    assert state["detail_requested"] is False


async def test_enabled_fire_or_smoke_uses_the_fire_instruction(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, "Sale mucho humo del cuarto de contadores"])
    assert state["current_node"] == "handle_emergency"
    assert "handle_emergency.fire_safety" in _templates(state)
    message = state["bot_messages"][-1].lower()
    assert "ascensor" in message and "112" in message


async def test_enabled_trapped_person_uses_the_general_instruction(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(manager, [*START, "El ascensor está parado y hay una señora dentro"])
    assert state["current_node"] == "handle_emergency"
    assert "handle_emergency.general_safety" in _templates(state)
    state = await manager.message(state["call_id"], "sí")
    [incident] = await repo.list_incidents()
    assert incident.urgency == "emergency"
    assert incident.category == "elevator"
    assert state["notice_channel"] == "call"


async def test_enabled_if_the_caller_says_it_is_not_an_emergency_it_is_treated_as_high(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(
        manager, [*START, "Huele a gas en el rellano", "No, no es una emergencia"]
    )
    assert state["urgency"] == "high"
    assert state["current_node"] == "confirm_summary"
    state = await manager.message(state["call_id"], "sí")
    [incident] = await repo.list_incidents()
    assert incident.urgency == "high"
    assert state["notice_channel"] == "email"


async def test_enabled_a_doubtful_answer_does_not_lose_the_emergency(
    manager: CallManager, repo: MemoryRepository
) -> None:
    """Ante la duda se registra: una emergencia nunca se queda en un bucle de repreguntas."""
    _activate(repo)
    state = await _converse(
        manager, [*START, "Huele a gas en el rellano", "Ay, madre mía, qué miedo"]
    )
    assert state["finished"] is True
    [incident] = await repo.list_incidents()
    assert incident.urgency == "emergency"
    assert any(t["action"] == "emergency_unconfirmed" for t in state["trace"])


async def test_enabled_normal_urgencies_do_not_change(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(
        manager, [*START, "Hay una fuga de agua en el baño y está inundando el suelo"]
    )
    assert state["urgency"] == "high"
    assert state["current_node"] == "confirm_summary"


@pytest.mark.parametrize(
    ("node", "expected"), [("handle_emergency", False), ("confirm_summary", True)]
)
def test_the_safety_instruction_is_not_paraphrased(node: str, expected: bool) -> None:
    """La redacción solo vigila números y nombres: podría perder «no encienda luces»."""
    state = {
        "current_node": node,
        "turn_timings": {"interpretation_origin": "llm"},
        "bot_messages": ["Texto corto. ¿Es correcto?"],
    }
    assert _deserves_phrasing(state, "lo que sea") is expected  # type: ignore[arg-type]


async def test_enabled_correcting_the_estate_reidentifies_without_losing_the_emergency(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    state = await _converse(
        manager, [*START, "Huele a gas en el rellano", "No, es en la avenida de la Alameda 5"]
    )
    assert state["current_node"] == "identify_estate"
    assert state["estate_id"] is None
    assert state["urgency"] is None
    assert state["declared_urgency"] == "emergency"
    assert state["request"]  # lo que contó no se pierde


async def test_enabled_without_estate_phone_uses_the_contact_phone(
    manager: CallManager, repo: MemoryRepository
) -> None:
    _activate(repo)
    estate = repo.estates["finca-mayor-12"]
    repo.estates["finca-mayor-12"] = estate.model_copy(update={"official_phone": ""})
    state = await _converse(manager, [*START, "Huele a gas en el rellano", "sí"])
    assert state["notice_channel"] == "call"
    assert state["notice_target"] == "+34910000001"  # contacto de la configuración


@pytest.mark.parametrize(
    ("text", "template"),
    [
        ("Huele a quemado en el cuarto de contadores", "handle_emergency.fire_safety"),
        ("Se está incendiando el contenedor", "handle_emergency.fire_safety"),
        ("Hay una fuga de gas", "handle_emergency.gas_safety"),
        ("Una señora atrapada en el ascensor", "handle_emergency.general_safety"),
    ],
)
def test_safety_instruction_by_keywords(text: str, template: str) -> None:
    assert _safety_instruction(text).template_id == template  # type: ignore[attr-defined]
