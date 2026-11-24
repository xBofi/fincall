"""Estado de una llamada dentro del grafo.

Cada turno de conversación es una invocación del grafo: el estado entra con `user_utterance`
y sale con `bot_messages` y `esperando_usuario=True` (o `finalizada=True`). El grafo decide el
siguiente nodo a partir de `current_node`; el LLM nunca elige nodos.

`pending` guarda datos que el vecino dijo antes de que se le preguntaran (interpretación de
turno); `next_step` los consume y salta las preguntas ya respondidas.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Trace(TypedDict):
    node: str
    action: str
    detail: str


class CallState(TypedDict, total=False):
    call_id: str
    started_at: str
    # Language of the call (ISO 639-1, `i18n` registry): what the bot speaks and how the
    # deterministic paths read the caller. `language_locked`: chosen explicitly (number, console,
    # the caller asking for it), so automatic detection no longer changes it
    language: str
    language_locked: bool
    caller_phone: str | None
    turn: int
    current_node: str
    user_utterance: str | None
    last_question: str | None
    # Lo último que dijo (o intentó decir) el bot y cuánto de ello llegó a sonar (ledger de salida)
    last_bot_message: str | None
    last_delivery: dict[str, Any] | None
    # Frases del último mensaje con su importancia, y los avisos que salieron en este turno
    bot_pieces: list[dict[str, Any]]
    turn_notices: list[str]
    # Turno de detalle del problema ya hecho (o no procede)
    detail_requested: bool
    # Emergencia ya atendida (indicación de seguridad dada y respuesta del vecino recogida)
    emergency_handled: bool
    bot_messages: list[str]
    history: list[dict[str, str]]
    trace: list[Trace]
    validations: list[str]
    awaiting_user: bool
    finished: bool
    # Tiempos del turno en curso (los rellenan los nodos; el gestor los registra)
    turn_timings: dict[str, Any]
    # Última interpretación LLM/determinista del turno (para la consola)
    interpretation: dict[str, Any] | None
    # Datos dichos por el vecino aún no consumidos por su nodo
    pending: dict[str, str]

    # Equipos de la finca (cargados al confirmarla) y si la clasificación ya los tuvo delante
    equipment_list: list[dict[str, Any]]
    equipment_evaluated: bool
    # A quién va la incidencia: equipo afectado, tipo de actuación y pega del vecino al resumen
    equipment_id: str | None
    work_type: str | None
    objection: str | None
    # Para el administrador o el profesional: equipo, contrato y por qué le llega
    suggestion: str | None

    # Configuración efectiva resuelta al identificar la finca
    config: dict[str, Any] | None
    config_id: str | None
    graph: str

    # Identificación
    estate_id: str | None
    estate_address: str | None
    candidate_estate_id: str | None
    estate_attempts: int
    home_id: str | None
    home_label: str | None
    # The caller has confirmed building + apartment read back together
    home_confirmed: bool | None
    # entrance / floor / door of the identified apartment, for reading it back in any language
    home_parts: dict[str, str] | None
    home_attempts: int

    # Solicitud
    request: str | None
    category: str | None
    urgency: str | None
    declared_urgency: str | None
    summary: str | None
    confidence_signals: dict[str, Any] | None
    confirmed: bool | None

    # Resolución
    specialist_id: str | None
    specialist_name: str | None
    notice_channel: str | None
    notice_target: str | None
    incident_id: str | None
    result: str | None
    review_reason: str | None


def initial_state(
    call_id: str, started_at: str, graph: str, language: str = "es", locked: bool = False
) -> CallState:
    return CallState(
        call_id=call_id,
        started_at=started_at,
        language=language,
        language_locked=locked,
        caller_phone=None,
        turn=0,
        current_node="next_step",
        user_utterance=None,
        last_question=None,
        last_bot_message=None,
        last_delivery=None,
        bot_pieces=[],
        turn_notices=[],
        detail_requested=False,
        emergency_handled=False,
        bot_messages=[],
        history=[],
        trace=[],
        validations=[],
        awaiting_user=False,
        finished=False,
        turn_timings={},
        interpretation=None,
        pending={},
        equipment_list=[],
        equipment_evaluated=False,
        equipment_id=None,
        work_type=None,
        objection=None,
        suggestion=None,
        config=None,
        config_id=None,
        graph=graph,
        estate_id=None,
        estate_address=None,
        candidate_estate_id=None,
        estate_attempts=0,
        home_id=None,
        home_label=None,
        home_confirmed=None,
        home_parts=None,
        home_attempts=0,
        request=None,
        category=None,
        urgency=None,
        declared_urgency=None,
        summary=None,
        confidence_signals=None,
        confirmed=None,
        specialist_id=None,
        specialist_name=None,
        notice_channel=None,
        notice_target=None,
        incident_id=None,
        result=None,
        review_reason=None,
    )
