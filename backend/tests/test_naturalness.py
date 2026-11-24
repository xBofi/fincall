"""El vecino habla a su manera; la máquina de estados se adapta sin perder el control."""

from __future__ import annotations

from typing import Any

from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager


async def _converse(manager: CallManager, turns: list[str]) -> dict:
    state = await manager.begin()
    for text in turns:
        state = await manager.message(state["call_id"], text)
    return state


async def test_everything_in_one_sentence_skips_already_answered_questions(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(
        manager,
        [
            "Hola, llamo desde Avenida de la Alameda 5, portal 2, tercero A. Soy Carmen "
            "y hay una fuga de agua en el baño",
            "sí",
        ],
    )
    # Tras confirmar la finca ya sabe la vivienda: lee las dos juntas una vez
    assert state["current_node"] == "confirm_home"
    assert state["bot_messages"][-1] == (
        "Entonces es Avenida de la Alameda 5, portal 2, 3º A. ¿Es correcto?"
    )
    state = await manager.message(state["call_id"], "sí")
    # El problema ya estaba dicho: directo al resumen
    assert state["current_node"] == "confirm_summary"
    assert state["home_id"] == "finca-alameda-5-2-3A"
    assert "declared_name" not in state and "name" not in state["interpretation"]
    assert state["category"] == "plumbing"
    questions = [h["text"] for h in state["history"] if h["role"] == "bot"]
    assert not any("¿Cuál es su vivienda?" in p for p in questions)
    assert not any("Dígame su nombre" in p for p in questions)
    state = await manager.message(state["call_id"], "sí")
    assert state["result"] == "incident_created"
    assert len(await repo.list_incidents()) == 1


async def test_loose_data_accumulates_and_is_used_later(manager: CallManager) -> None:
    state = await _converse(
        manager,
        ["Calle Mayor 12", "sí", "Cuarto A. Me llamo Pedro y no hay luz en la escalera", "sí"],
    )
    assert state["home_id"] == "finca-mayor-12-u-4A"
    assert "declared_name" not in state and "name" not in state["interpretation"]
    assert state["request"]
    assert state["current_node"] == "confirm_summary"


async def test_asking_to_repeat_repeats_the_question(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "¿Cómo? No le he oído bien"])
    assert state["current_node"] == "identify_home"
    assert "le repito." in state["bot_messages"][-1]
    assert "vivienda" in state["bot_messages"][-1]


async def test_caller_question_is_answered_and_resumed(manager: CallManager) -> None:
    state = await _converse(manager, ["¿Esta llamada se está grabando?"])
    assert state["current_node"] == "identify_estate"
    assert "se registra" in state["bot_messages"][-1]
    assert "finca" in state["bot_messages"][-1]
    assert state["estate_attempts"] == 0  # no cuenta como intento fallido


async def test_out_of_scope_explains_and_resumes(manager: CallManager) -> None:
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "2 B", "sí", "¿Cuánto pago de cuota al mes?"]
    )
    assert state["current_node"] == "collect_request"
    assert "solo puedo gestionar incidencias" in state["bot_messages"][-1].lower()


async def test_asking_for_a_person_hands_off_to_the_administrator(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "Quiero hablar con una persona"])
    assert state["finished"] is True
    assert state["result"] == "handed_to_administrator"
    assert state["notice_target"] == "mayor12@demo.test"
    assert repo.calls[state["call_id"]].result == "handed_to_administrator"


async def test_farewell_closes_the_call(
    manager: CallManager, repo: MemoryRepository
) -> None:
    state = await _converse(manager, ["Adiós, ya llamaré en otro momento"])
    assert state["finished"] is True
    assert state["result"] == "abandoned"
    assert await repo.list_incidents() == []


async def test_rejecting_candidate_with_another_address_does_not_use_an_attempt(
    manager: CallManager,
) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "No, no, la avenida de la Alameda 5"])
    assert state["candidate_estate_id"] == "finca-alameda-5"
    assert state["estate_attempts"] == 0
    assert "Alameda 5" in state["bot_messages"][-1]


async def test_correction_in_the_summary_reclassifies(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "3 B", "No hay luz en la escalera"])
    assert state["category"] == "electrical"
    state = await manager.message(
        state["call_id"], "No, me he equivocado, es una fuga de agua en el baño"
    )
    assert state["category"] == "plumbing"
    assert state["current_node"] == "confirm_summary"
    assert state["bot_messages"][0] == "De acuerdo, lo corrijo."


async def test_home_correction_in_the_summary(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "3 B", "No hay luz en la escalera"])
    state = await manager.message(state["call_id"], "No, antes le he dicho 3 B pero es 3 A")
    assert state["home_id"] == "finca-mayor-12-u-3A"
    # La vivienda corregida se vuelve a leer antes del resumen
    assert state["current_node"] == "confirm_home"
    assert "3º A" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "sí")
    assert state["home_confirmed"] is True
    assert state["current_node"] == "confirm_summary"


async def test_deterministic_turns_do_not_call_the_llm(manager: CallManager, llm: Any) -> None:
    await _converse(manager, ["Calle Mayor 12", "sí", "3 B"])
    # dirección corta, sí/no y vivienda por regex: cero llamadas al modelo
    assert llm.calls == []
    aggregates = manager.metrics.aggregates()
    assert aggregates["turns"] == 4
    assert aggregates["server_ms_without_llm"]["n"] == 4


async def test_metrics_record_llm_calls_and_timings(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "3 B", "No hay luz en la escalera"])
    turns = manager.metrics.turns(state["call_id"])
    last = turns[-1]
    assert last.llm_calls == 1  # interpretación fusionada con clasificación
    assert last.interpretation_origin == "llm"
    assert last.server_ms is not None
    assert state["turn_timings"]["server_ms"] >= 0
    completed_turn = manager.metrics.complete_client(
        state["call_id"], last.turn, {"response_ms": 1100, "vad_silence_ms": 500}
    )
    assert completed_turn is not None and completed_turn.rating == "target"
    assert manager.metrics.aggregates()["ratings"]["target"] == 1


async def test_unrecognisable_address_does_not_hand_off_to_human(manager: CallManager, llm: Any) -> None:
    # El modelo se equivoca y marca "humano" ante una transcripción sin sentido
    original = llm._interpret
    llm._interpret = lambda user, schema: {"intent": "human"}  # type: ignore[method-assign]
    state = await _converse(manager, ["Estoy en la finca Carregüerdor del Saford, Doctor Mood"])
    llm._interpret = original  # type: ignore[method-assign]
    assert state["finished"] is False
    assert state["current_node"] == "identify_estate"
    assert state["estate_attempts"] == 1
    assert "repetirme la calle" in state["bot_messages"][-1]


async def test_nonexistent_home_explains_the_estate_layout(
    manager: CallManager,
) -> None:
    # El piso existe: solo se ofrecen sus puertas
    state = await _converse(manager, ["Calle Mayor 12", "sí", "piso 1, puerta 2"])
    assert state["current_node"] == "identify_home"
    assert "En el 1º las puertas son A o B" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "piso 1 puerta B")
    assert state["home_id"] == "finca-mayor-12-u-1B"
    # Tampoco existe el piso: se describe la finca entera
    state = await _converse(manager, ["Calle Mayor 12", "sí", "piso 9, puerta 2"])
    assert "pisos del 1 al 4" in state["bot_messages"][-1]
    assert "puertas A o B" in state["bot_messages"][-1]


async def test_greeting_does_not_count_as_estate_attempt(manager: CallManager) -> None:
    state = await _converse(manager, ["Hola, buenas."])
    assert state["estate_attempts"] == 0
    assert state["bot_messages"][-1].startswith("Hola, buenas.")
    state = await manager.message(state["call_id"], "Avenida de la Alameda 5")
    assert state["candidate_estate_id"] == "finca-alameda-5"


async def test_floor_and_door_in_several_entrances_asks_the_entrance(manager: CallManager) -> None:
    state = await _converse(manager, ["Alameda 5", "sí", "El tercero A es mi vivienda"])
    assert state["current_node"] == "identify_home"
    assert state["home_attempts"] == 0
    assert "¿En qué portal vive, 1 o 2?" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "portal 1")
    assert state["home_id"] == "finca-alameda-5-1-3A"


async def test_the_name_is_neither_kept_nor_read_back(manager: CallManager) -> None:
    """2026-10-22: the caller's name is not stored. Only what they said keeps it."""
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "3 B", "Soy Lauragomera y no hay luz en la escalera"]
    )
    # The test model leaves the name inside the problem; the real one separates it
    assert state["bot_messages"][-1].startswith("Le resumo: Soy Lauragomera y no hay luz")
    assert "3º B" not in state["bot_messages"][-1]
    assert "declared_name" not in state and "name" not in state["interpretation"]


async def test_closing_explains_the_next_step(manager: CallManager) -> None:
    state = await manager.begin(caller_phone="+34600000000")
    for t in ["Calle Mayor 12", "sí", "3 B", "Gotea el grifo del patio", "sí"]:
        state = await manager.message(state["call_id"], t)
    final = state["bot_messages"][-1]
    assert "Fontanería Pérez" in final
    assert "le llamará a este mismo número" in final
    assert "no puedo darle hora" in final.lower() or "no puedo confirmarle la hora" in final.lower()
    assert "veinticuatro horas" in final


async def test_home_with_fillers_uses_the_regex_and_does_not_invent_entrance(
    manager: CallManager, llm: Any
) -> None:
    original = llm._interpret
    # El modelo se inventa un portal en una finca sin portales
    llm._interpret = lambda user, schema: {
        "entrance": "a",
        "floor": "1",
        "door": "A",
        "confirms": True,
    }  # type: ignore[method-assign]
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "Ah, vale, perdón. Pues sí, piso 1, puerta A."]
    )
    llm._interpret = original  # type: ignore[method-assign]
    assert state["home_id"] == "finca-mayor-12-u-1A"


async def test_comment_about_the_bot_is_not_a_correction(
    manager: CallManager, llm: Any
) -> None:
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "1 A", "Hay que pintar las paredes del corredor"]
    )
    # El resumen dice lo entendido, no la categoría interna (2026-10-21)
    assert "pintar las paredes" in state["bot_messages"][-1]
    assert "no he podido" not in state["bot_messages"][-1].lower()
    original = llm._interpret
    llm._interpret = lambda user, schema: {
        "problem": "has podido clasificarlo con urgencia baja"
    }  # type: ignore[method-assign]
    state = await manager.message(
        state["call_id"], "Dirás que has podido clasificarlo con una urgencia baja."
    )
    llm._interpret = original  # type: ignore[method-assign]
    assert state["current_node"] == "confirm_summary"
    assert "Responda sí o no" in state["bot_messages"][-1]
    assert state["request"].startswith("Hay que pintar")


async def test_yes_transcribed_as_a_letter_confirms(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "S."])
    assert state["estate_id"] == "finca-mayor-12"
    assert llm.calls == []


async def test_wait_filler_does_not_use_an_attempt(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "Ah, vale, espéreme."])
    assert state["current_node"] == "identify_home"
    assert state["home_attempts"] == 0
    assert "sin prisa" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "piso 1, puerta A")
    assert state["home_id"] == "finca-mayor-12-u-1A"


async def test_partial_home_data_accumulates(manager: CallManager, llm: Any) -> None:
    original = llm._interpret
    llm._interpret = lambda user, schema: {
        "floor": "3"
    }  # el STT perdió la puerta  # type: ignore[method-assign]
    state = await _converse(manager, ["Alameda 5", "sí", "El tercero es mi vivienda"])
    assert state["home_attempts"] == 0
    assert "¿Y la puerta?" in state["bot_messages"][-1]
    # Lo que se dice al vecino va en español (el código en inglés dejó «floor 3», 2026-10-22)
    assert "Tomo nota: piso 3." in state["bot_messages"][-1]
    llm._interpret = lambda user, schema: {"entrance": "1"}  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "Portal 1")
    assert "¿Y la puerta?" in state["bot_messages"][-1]
    assert "Tomo nota: portal 1, piso 3." in state["bot_messages"][-1]
    llm._interpret = original  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "puerta A")
    assert state["home_id"] == "finca-alameda-5-1-3A"


async def test_affirmation_with_stt_noise(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "Ese, eso es."])
    assert state["estate_id"] == "finca-mayor-12"
    assert llm.calls == []


def test_empty_model_markers_are_not_data() -> None:
    from building_call.llm.interpretation import normalize_interpretation

    i = normalize_interpretation({"name": "No especificado", "floor": "3", "door": "null"})
    assert i.name is None and i.door is None and i.floor == "3"


async def test_faq_without_question_marks(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["Esta llamada se está grabando."])
    assert "se registra" in state["bot_messages"][-1]
    assert state["estate_attempts"] == 0


async def test_detail_question_before_the_summary(detail_manager: CallManager) -> None:
    manager = detail_manager
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "1 A", "No funciona la luz del pasillo del segundo"]
    )
    assert state["current_node"] == "ask_detail"
    assert "electricista" in state["bot_messages"][-1]
    state = await manager.message(state["call_id"], "Son todas las del pasillo, no encienden")
    assert state["current_node"] == "confirm_summary"
    assert "Detalle: Son todas las del pasillo" in state["request"]
    state = await manager.message(state["call_id"], "sí")
    assert state["result"] == "incident_created"


async def test_long_description_does_not_ask_detail(detail_manager: CallManager) -> None:
    manager = detail_manager
    long = (
        "No funciona ninguna de las luces del pasillo del segundo piso desde ayer por la noche, "
        "no encienden aunque le des al interruptor y el resto del edificio tiene luz"
    )
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", long])
    assert state["current_node"] == "confirm_summary"


async def test_repeat_returns_the_full_message_and_hello_after_unheard_message(
    manager: CallManager, llm: Any
) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    summary = state["bot_messages"][-1]
    assert summary.startswith("Le resumo")
    # El navegador informa de que el mensaje se cortó al 20 %
    await manager.record_delivery(
        state["call_id"], state["turn"], "interrupted", 0.2, "Le re"
    )
    n = len(llm.calls)
    state = await manager.message(state["call_id"], "¿Hola? ¿Estás ahí?")
    assert len(llm.calls) == n  # determinista
    assert state["bot_messages"][-1] == f"Perdone, le repito. {summary}"
    assert state["current_node"] == "confirm_summary"
    state = await manager.message(state["call_id"], "¿Me lo repites?")
    assert summary in state["bot_messages"][-1]


async def test_the_name_is_not_asked(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "sí"])
    assert state["current_node"] == "collect_request"
    assert not any("nombre" in m for m in state["bot_messages"])
    state = await manager.message(state["call_id"], "¿Para qué necesita mi nombre?")
    assert "No necesito su nombre" in state["bot_messages"][-1]
    assert state["current_node"] == "collect_request"


async def test_name_said_unprompted_is_not_kept(manager: CallManager) -> None:
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A, soy Ana", "sí"])
    assert "Ana" not in str({k: v for k, v in state.items() if k != "history"})
    assert state["bot_messages"][-1].startswith("Perfecto. Cuénteme")


async def test_cut_phrase_when_giving_home_is_not_taken_as_fault(
    manager: CallManager, llm: Any
) -> None:
    # Llamada real 2026-10-11: el STT oyó "Primero abre la" y el modelo lo devolvió como problema
    # no clasificable; el bot se saltó "¿qué ocurre?" y pidió detalles de una avería inexistente.
    original = llm._interpret
    llm._interpret = lambda user, schema: {  # type: ignore[method-assign]
        "problem": "Primero abre la",
        "category": "unclassifiable",
    }
    state = await _converse(manager, ["Calle Mayor 12", "sí", "Primero abre la"])
    llm._interpret = original  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "Piso 1, puerta A.")
    assert state["home_id"] == "finca-mayor-12-u-1A"
    state = await manager.message(state["call_id"], "sí")
    assert state["current_node"] == "collect_request"
    assert not state.get("request")


async def test_nonexistent_door_with_valid_floor_asks_only_the_door(
    manager: CallManager, llm: Any
) -> None:
    original = llm._interpret
    llm._interpret = lambda user, schema: {"floor": "1", "door": "1"}  # type: ignore[method-assign]
    state = await _converse(manager, ["Calle Mayor 12", "sí", "La puerta primero."])
    llm._interpret = original  # type: ignore[method-assign]
    last = state["bot_messages"][-1]
    assert "No encuentro esa vivienda" not in last
    assert "A o B" in last
    assert state["pending"].get("floor") == "1"
    state = await manager.message(state["call_id"], "puerta A")
    assert state["home_id"] == "finca-mayor-12-u-1A"


async def test_repeating_twice_does_not_stack_the_tag(manager: CallManager) -> None:
    state = await _converse(
        manager, ["Calle Mayor 12", "sí", "¿Cómo? No le he oído bien", "¿Cómo? No le he oído bien"]
    )
    assert state["bot_messages"][-1].count("le repito") == 1
    assert "vivienda" in state["bot_messages"][-1]


async def test_bare_yes_to_the_address_does_not_call_the_llm(
    manager: CallManager, llm: Any
) -> None:
    # Con Gemini, "Sí." ante "¿en qué finca?" tardaba 1,4 s en decir que no era una dirección
    state = await _converse(manager, ["Sí."])
    assert llm.calls == []
    assert state["current_node"] == "identify_estate"
    assert "calle y el número" in state["bot_messages"][-1]


async def test_greeting_announces_the_ai_and_the_recording(manager: CallManager) -> None:
    state = await manager.begin()
    greeting = state["bot_messages"][0].lower()
    assert "inteligencia artificial" in greeting
    assert "grabada" in greeting
    # Repetir la pregunta no vuelve a leer el aviso entero
    assert state["last_question"].startswith("¿En qué finca")


# ------------------------------------------------- enrutamiento: las palabras sueltas no deciden
# Detectores por subcadena secuestraban incidencias reales (2026-10-21): "cuando" o "tarda" se
# respondían como pregunta frecuente, "repite" como petición de repetir y "espere" como muletilla.


async def test_incidents_with_faq_words_are_recorded_as_incidents(
    manager: CallManager,
) -> None:
    cases = {
        "El ascensor tarda mucho en venir": "elevator",
        "La luz de la escalera se apaga cuando subo": "electrical",
        "Pierde agua el registro del portal": "plumbing",
    }
    for phrase, category in cases.items():
        state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", phrase])
        assert state["current_node"] == "confirm_summary", phrase
        assert state["request"] == phrase
        assert state["category"] == category


async def test_repeat_inside_a_fault_is_not_a_repeat_request(manager: CallManager) -> None:
    phrase = "Se repite el ruido de la caldera"
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", phrase])
    assert state["current_node"] == "confirm_summary"
    assert state["request"] == phrase


async def test_wait_inside_a_fault_is_not_a_filler(manager: CallManager) -> None:
    phrase = "Espere, que el ascensor no va"
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", phrase])
    assert state["current_node"] == "confirm_summary"
    assert state["category"] == "elevator"


def test_detectors_only_accept_the_phrase_with_fillers() -> None:
    from building_call.graph.nodes import _did_not_hear, _is_filler, _is_greeting

    assert _did_not_hear("¿Cómo? No le he oído bien", {})
    assert _did_not_hear("Perdone, ¿me lo repite?", {})
    assert not _did_not_hear("Se repite el ruido", {})
    assert not _did_not_hear("Como dice el portero, no hay luz", {})
    assert _is_filler("Ah, vale, espéreme.")
    assert _is_filler("Un momento, por favor")
    assert not _is_filler("Espere que el ascensor no va")
    assert _is_greeting("Hola, buenas tardes")
    assert not _is_greeting("Hola, el ascensor no va")


async def test_faq_is_interpreted_by_the_model_and_answered(manager: CallManager, llm: Any) -> None:
    state = await _converse(manager, ["¿Esta llamada se está grabando?"])
    assert "se registra" in state["bot_messages"][-1]
    assert state["estate_attempts"] == 0
    assert len(llm.calls) == 1


async def test_fault_without_element_asks_what_fails_and_reclassifies(
    detail_manager: CallManager,
) -> None:
    manager = detail_manager
    state = await _converse(manager, ["Calle Mayor 12", "sí", "1 A", "sí", "Está parado"])
    assert state["category"] == "unclassifiable"
    assert state["current_node"] == "ask_detail"
    assert "qué es lo que" in state["bot_messages"][-1].lower()
    state = await manager.message(
        state["call_id"], "El ascensor, entre el segundo y el tercero"
    )
    assert state["current_node"] == "confirm_summary"
    assert state["category"] == "elevator"


async def test_cut_phrase_without_category_is_not_taken_as_fault_either(
    manager: CallManager, llm: Any
) -> None:
    # Si el modelo devuelve el problema sin categoría, el hueco era el mismo
    original = llm._interpret
    llm._interpret = lambda user, schema: {"problem": "Primero abre la"}  # type: ignore[method-assign]
    state = await _converse(manager, ["Calle Mayor 12", "sí", "Primero abre la"])
    llm._interpret = original  # type: ignore[method-assign]
    state = await manager.message(state["call_id"], "Piso 1, puerta A.")
    state = await manager.message(state["call_id"], "sí")
    assert state["current_node"] == "collect_request"
    assert not state.get("request")

