"""Nodos del flujo de llamada.

Reglas (notas de arquitectura): el LLM interpreta y extrae; el código valida y decide.
Cada nodo es una función reutilizable por cualquier grafo del registro.

Naturalidad: cada turno del vecino pasa por una interpretación (determinista si basta, LLM si
no) que extrae todo lo que dijo y una intención global. `next_step` consume esos datos y
salta las preguntas ya respondidas; las intenciones globales (repetir, corregir, humano,
colgar, fuera de ámbito, pregunta) se atienden desde cualquier nodo.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from time import perf_counter
from typing import Any
from uuid import uuid4

from building_call import i18n
from building_call.domain.config import (
    DEFAULTS,
    UNCLASSIFIABLE_CATEGORY,
    URGENCIES,
    EffectiveConfig,
)
from building_call.domain.entities import WORK_TYPES, Call, Home, Incident
from building_call.graph.ledger import heard_text_if_cut, invalid_confirmation
from building_call.graph.manifest import verify_transition
from building_call.graph.state import CallState, Trace
from building_call.graph.templates import CATALOG as TEMPLATE_CATALOG
from building_call.graph.templates import render
from building_call.i18n.formatting import format_date
from building_call.i18n.models import Phrases
from building_call.i18n.parsing import Parser
from building_call.llm import wire
from building_call.llm.base import LanguageModel
from building_call.llm.interpretation import (
    Interpretation,
    interpret,
    interpretation_version,
)
from building_call.llm.prompts import (
    classification_schema,
    classification_system_prompt,
    classification_version,
)
from building_call.nlp.hybrid import HybridRouter
from building_call.repos.base import Repository

logger = logging.getLogger(__name__)

ESTATE_THRESHOLD = 0.6  # Pendiente: calibrar con datos reales (notas de diseño)


@dataclass
class Dependencies:
    repo: Repository
    llm: LanguageModel
    # Interpretaciones lanzadas sobre transcripts parciales (antes del fin de turno): por llamada
    # y, dentro, por clave normalizada del texto. `_interpret` las reutiliza si el final
    # coincide con el parcial. Separadas por llamada: con varias a la vez, una no puede cancelar
    # ni reutilizar lo que dijo el vecino de otra.
    speculations: dict[str, dict[str, asyncio.Future[Interpretation]]] = field(
        default_factory=dict
    )
    # Hybrid routing (BC_NLP_HYBRID): the turn classifier answers before the LLM when it is sure
    router: HybridRouter | None = None
    # Tools the agent can call during the call (n8n webhooks, `integrations.n8n_tool`). None =
    # no tools: the flow is exactly the one without integrations.
    tools: Any = None

    def discard_speculations(self, call_id: str) -> None:
        for future in self.speculations.pop(call_id, {}).values():
            future.cancel()


def speculation_key(text: str) -> str:
    """Clave para casar un transcript parcial con el final (sin puntuación ni mayúsculas)."""
    return _basic_normalize(text)


def _p() -> Parser:
    """The deterministic rules of the call's language (`i18n`: set around each turn)."""
    return i18n.parser()


def _phrases() -> Phrases:
    return i18n.current().phrases


def spoken_category(category: str) -> str:
    return _p().spoken_category(category)


def _capitalize_first(text: str) -> str:
    """Like `str.capitalize` but without lowercasing the rest ("Smith Plumbing")."""
    return text[:1].upper() + text[1:]


def equipment_for(state: CallState) -> list[tuple[str, str]] | None:
    """(id, description) of the building's equipment, so the model knows which one is affected."""
    return [(e["id"], e["description"]) for e in state.get("equipment_list", [])] or None


def catalog_for(state: CallState) -> list[str]:
    return _catalog(state)


# --------------------------------------------------------------------------- normalization
# The rules live in `i18n.parsing`, per language; these names keep the nodes readable.


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _basic_normalize(text: str) -> str:
    return _p().normalize(text)


def _numbers_to_digits(text: str) -> str:
    return _p().numbers_to_digits(_basic_normalize(text))


def _normalize(text: str) -> str:
    """Normalization for addresses: spelled-out numbers to digits and no stop words."""
    return _p().normalize_address(text)


def _address_similarity(utterance: str, address: str) -> float:
    return _p().address_similarity(utterance, address)


def _only_address(utterance: str, address: str) -> bool:
    return _p().only_address(utterance, address)


def _yes_no_answer(text: str) -> bool | None:
    return _p().yes_no(text)


def _moves_on_to_problem(interp: Interpretation, utterance: str) -> bool:
    return _p().moves_on_to_problem(interp.problem, interp.address, utterance)


def _ordinal(floor: str) -> str:
    return _p().ordinal(floor)


def _normalize_entrance(value: str | None) -> str | None:
    return _p().normalize_entrance(value)


def _normalize_floor(value: str | None) -> str | None:
    return _p().normalize_floor(value)


def _normalize_door(value: str | None) -> str | None:
    return _p().normalize_door(value)


def _extract_home_regex(text: str) -> tuple[str | None, str | None, str | None]:
    return _p().extract_home(text)


def _home_candidates(
    homes: list[Home], entrance: str | None, floor: str | None, door: str | None
) -> list[Home]:
    """Apartments that match what was said. Several => the entrance is missing; none => it
    does not exist."""
    candidates = [
        v
        for v in homes
        if _normalize_floor(v.floor) == floor and _normalize_door(v.door) == door
    ]
    if entrance is not None:
        candidates = [v for v in candidates if _normalize_entrance(v.entrance) == entrance]
    return candidates


def _is_real_correction(fresh: str, previous: str, utterance: str) -> bool:
    return _p().is_real_correction(fresh, previous, utterance)


def _did_not_hear(text: str, state: CallState) -> bool:
    return _p().did_not_hear(text, state.get("last_delivery"))


def _is_filler(text: str) -> bool:
    return _p().is_filler(text)


def _is_greeting(text: str) -> bool:
    return _p().is_greeting(text)


# --------------------------------------------------------------------------- who is notified

def _suggestion(
    work_type: str,
    objection: str | None,
    equipment: dict[str, Any] | None,
    maintainer: Any,
    proposed: Any,
) -> str | None:
    """Text for whoever receives the issue: equipment, contract and why it reaches them."""
    language = i18n.current()
    ph = language.phrases
    parts: list[str] = []
    if objection:
        parts.append(ph.objection.format(objection=objection))
    if work_type in language.work_type_reasons:
        parts.append(language.work_type_reasons[work_type])
    if equipment:
        data = [ph.equipment.format(description=equipment["description"])]
        if maintainer:
            data.append(ph.maintainer.format(name=maintainer.name, target=maintainer.target))
        if equipment.get("contract"):
            data.append(ph.contract.format(contract=equipment["contract"]))
        if equipment.get("warranty_until"):
            data.append(ph.warranty.format(date=_date(equipment["warranty_until"])))
        parts.append(", ".join(data) + ".")
    if proposed is not None and (objection or work_type != "repair"):
        parts.append(ph.proposed.format(name=proposed.name))
    return " ".join(parts) or None


def _date(value: Any) -> str:
    """A stored ISO date as the call's language writes it ('Nov 24, 2026', '24 nov 2026')."""
    try:
        return format_date(date.fromisoformat(str(value)))
    except ValueError:
        return str(value)


def _emergency_suggestion(state: CallState) -> str | None:
    """An emergency's issue starts by saying that the alerts are still pending."""
    suggestion = state.get("suggestion")
    if state.get("urgency") != "emergency":
        return suggestion
    note = _phrases().emergency_note
    return f"{note} {suggestion}" if suggestion else note


# --------------------------------------------------------------------------- utilidades de estado


#: Transiciones observadas que el manifiesto no declara. Se exponen en `/api/metrics` para
#: que una arista sin declarar se vea, en vez de romper la llamada de un vecino.
MANIFEST_VIOLATIONS: list[str] = []


def _verify(state: CallState, target: str) -> None:
    """Comprueba la transición contra el manifiesto. Bajo pytest lanza; en producción, anota."""
    reason = verify_transition(state.get("current_node"), target)
    if reason is not None:
        logger.warning("Transición no declarada en el manifiesto: %s", reason)
        if reason not in MANIFEST_VIOLATIONS:
            MANIFEST_VIOLATIONS.append(reason)


def _with_trace(state: CallState, node: str, action: str, detail: str = "") -> list[Trace]:
    return [*state.get("trace", []), Trace(node=node, action=action, detail=detail)]


def _annotate_template(state: CallState, node: str, text: Any, extra: dict[str, Any]) -> None:
    """Deja en la traza de qué plantilla y versión salió el mensaje.

    En cuanto los textos son editables, una auditoría que solo guarde la frase no permite saber
    qué se dijo ni con qué versión. `render` devuelve un `str` que lleva su propia identidad, así
    que anotarlo no cuesta cablear el id por cada firma.
    """
    template_id = getattr(text, "template_id", None)
    if template_id is None:
        return
    if TEMPLATE_CATALOG[template_id].notice:
        # El aviso es lo que va antes de la pregunta (el saludo acaba preguntando la finca)
        start = _p().question_start(str(text))
        notice = str(text)[:start].strip() if start else str(text)
        extra["turn_notices"] = [*state.get("turn_notices", []), notice]
    base = extra.get("trace", state.get("trace", []))
    extra["trace"] = [
        *base,
        Trace(node=node, action="template", detail=f"{template_id} v{text.version}"),
    ]


def _ask(state: CallState, node: str, text: str, **extra: Any) -> dict[str, Any]:
    # `history` en extra ya incluye el turno del vecino; el mensaje del bot se añade después.
    _verify(state, node)
    _annotate_template(state, node, text, extra)
    base = extra.pop("history", state.get("history", []))
    return {
        "current_node": node,
        "awaiting_user": True,
        "user_utterance": None,
        **extra,
        "bot_messages": [*state.get("bot_messages", []), text],
        "history": [*base, {"role": "bot", "text": text}],
        # Para repetir/retomar se guarda solo la pregunta, sin saludos ni avisos
        "last_question": _question_only(text),
    }


#: Templates that start with a greeting before their question
_GREETING_TEMPLATES = frozenset({"identify_estate.greeting", "identify_estate.greeting_reply"})


def _question_only(text: str) -> str:
    if getattr(text, "template_id", None) not in _GREETING_TEMPLATES:
        return text
    start = _p().question_start(text)
    return text[start:] if start else text


def _advance(
    state: CallState, node: str, message: str | None = None, **extra: Any
) -> dict[str, Any]:
    _verify(state, node)
    _annotate_template(state, node, message, extra)
    base = extra.pop("history", state.get("history", []))
    changes: dict[str, Any] = {
        "current_node": node,
        "awaiting_user": False,
        "user_utterance": None,
        "history": base,
        **extra,
    }
    if message:
        changes["bot_messages"] = [*state.get("bot_messages", []), message]
        changes["history"] = [*base, {"role": "bot", "text": message}]
    return changes


def _record_utterance(state: CallState) -> list[dict[str, str]]:
    utterance = state.get("user_utterance")
    if utterance is None:
        return state.get("history", [])
    return [*state.get("history", []), {"role": "caller", "text": utterance}]


def _config(state: CallState) -> EffectiveConfig:
    doc = state.get("config")
    return EffectiveConfig.model_validate(doc) if doc else DEFAULTS


def _catalog(state: CallState) -> list[str]:
    return [*_config(state).category_catalog, UNCLASSIFIABLE_CATEGORY]


def _timings(state: CallState, **values: Any) -> dict[str, Any]:
    t = dict(state.get("turn_timings", {}))
    for k, v in values.items():
        if k == "llm_calls":
            t[k] = t.get(k, 0) + v
        else:
            t[k] = v
    return t


def _call_from(state: CallState, result: str, reason: str | None = None) -> Call:
    return Call(
        id=state["call_id"],
        started_at=datetime.fromisoformat(state["started_at"]),
        caller_phone=state.get("caller_phone"),
        estate_id=state.get("estate_id"),
        home_id=state.get("home_id"),
        transcript=state.get("history", []),
        summary=state.get("summary") or state.get("request"),
        result=result,  # type: ignore[arg-type]
        review_reason=reason,
        config_id=state.get("config_id"),
        graph=state.get("graph"),
    )


# --------------------------------------------------------------------------- interpretación


def _without_repeat_tag(text: str) -> str:
    """'Sure, let me repeat that. X' -> 'X', so repeating twice does not chain the tag."""
    tag_phrases = tuple(render(c, message="") for c in ("global.repeat", "global.not_heard"))
    while text.startswith(tag_phrases):
        text = next(text[len(c) :] for c in tag_phrases if text.startswith(c))
    return text


# Mientras se piden finca o vivienda, un "problema" que el modelo no sabe clasificar suele ser la
# respuesta mal transcrita ("Primero abre la"), no una avería: guardarlo saltaba "¿qué ocurre?".
_IDENTIFICATION_NODES = frozenset({"identify_estate", "identify_home", "confirm_home"})
#: "Primero abre la": lo que el STT deja de una respuesta cortada cabe en pocas palabras
_CUT_PHRASE_WORDS = 3


def known_context(state: CallState) -> dict[str, str] | None:
    """What the call has already established, for the model: the building and the apartment.
    With it, "I meant the B" comes back as the whole apartment. The prompt words it."""
    known: dict[str, str] = {}
    if state.get("estate_id") and state.get("estate_address"):
        known["address"] = str(state["estate_address"])
    home = state.get("home_parts") or {}
    if not home:
        pending = state.get("pending", {})
        home = {k: pending[k] for k in ("entrance", "floor", "door") if pending.get(k)}
    known.update({k: str(v) for k, v in home.items() if v})
    return known or None


def _mid_sentence(text: str) -> str:
    """'The elevator is stuck' -> 'the elevator is stuck', to read it after 'right: '."""
    return _p().mid_sentence(text)


def _home_parts(home: Home) -> dict[str, str]:
    parts = {"floor": home.floor, "door": home.door}
    if home.entrance:
        parts["entrance"] = home.entrance
    return parts


def _home_label(home: Home) -> str:
    """The apartment as the call's language writes it ('Apt 3A', '3º B')."""
    return _p().home_label(home.entrance, home.floor, home.door)


def _spoken_home(state: CallState) -> str:
    """The apartment as it is read out: 'entrance 1, apartment 3A', 'tercero B'."""
    parts = state.get("home_parts") or {}
    if not parts.get("floor"):
        return state.get("home_label") or ""
    return _p().spoken_home(parts.get("entrance"), parts["floor"], parts.get("door", ""))


def _is_emergency(state: CallState, queued: dict[str, str]) -> bool:
    """Only when emergency handling is on: otherwise the call follows the normal flow."""
    urgencies = (state.get("urgency"), state.get("declared_urgency"), queued.get("urgency"))
    return "emergency" in urgencies and _config(state).emergencies_enabled


def _merge_pending(state: CallState, interp: Interpretation) -> dict[str, str]:
    queued = dict(state.get("pending", {}))
    # Sin categoría del catálogo tampoco vale: el modelo puede omitirla en vez de decir
    # no_clasificable. Pero solo en frases muy cortas: "el portero automático no funciona" no
    # tiene categoría propia y es una avería de verdad (2026-10-21)
    doubtful_problem = (
        interp.category in (None, UNCLASSIFIABLE_CATEGORY)
        and state.get("current_node") in _IDENTIFICATION_NODES
        and len((interp.problem or "").split()) <= _CUT_PHRASE_WORDS
        # "Huele a gas" is short and has no category, but it is never a cut phrase
        and interp.urgency != "emergency"
    )
    # The name is never kept (2026-10-22): the address and the home are enough
    for attr in ("address", "entrance", "floor", "door", "problem"):
        value = getattr(interp, attr)
        if value and not (attr == "problem" and doubtful_problem):
            queued[attr] = value
    if interp.problem and not doubtful_problem:
        queued["short_problem"] = interp.problem
        for field in ("category", "urgency", "work_type"):
            value = getattr(interp, field)
            if value:
                queued[field] = value
        if interp.equipment and interp.equipment in {e["id"] for e in state.get("equipment_list", [])}:
            queued["equipment"] = interp.equipment
        if state.get("equipment_list"):
            # La clasificación ya tuvo delante los equipos: no hace falta repetirla
            queued["equipment_evaluated"] = "1"
    return queued


def _asks_for_person(text: str) -> bool:
    return _p().asks_for_person(text)


def _says_goodbye(text: str) -> bool:
    return _p().says_goodbye(text)


def _safety_instruction(text: str) -> str:
    """Safety advice for an emergency, by what the caller says (gas, fire, the rest)."""
    kind = _p().safety_kind(text)
    if kind == "gas":
        return render("handle_emergency.gas_safety")
    if kind == "fire":
        return render("handle_emergency.fire_safety")
    return render("handle_emergency.general_safety")


def _answer_question(text: str) -> str:
    return _p().answer_question(text)


def build_nodes(deps: Dependencies) -> dict[str, Any]:
    """Devuelve {nombre_nodo: función async} para registrarlos en un grafo."""

    async def _interpret(
        state: CallState, utterance: str, node: str, *, yes_no: bool = False
    ) -> tuple[Interpretation, dict[str, Any]]:
        """Determinista cuando basta; si no, una llamada al LLM. Devuelve (interp, tiempos)."""
        if yes_no:
            r = _yes_no_answer(utterance)
            # A "no" read only from "isn't" / "it's not" is usually the problem itself
            # ("the intercom isn't working"): that one goes to the model
            words = _basic_normalize(utterance).split()
            if r is not None and len(words) <= 4 and (r or _p().explicit_no(utterance)):
                interp = Interpretation(confirms=r, origin="deterministic")
                return interp, _timings(state, interpretation_origin="deterministic")
        if deps.router is not None:
            # With equipment in the estate the LLM must choose the affected one: no problem shape
            catalog = None if equipment_for(state) else _catalog(state)
            routed = await asyncio.to_thread(deps.router.interpret, utterance, node, catalog)
            if routed is not None:
                deps.discard_speculations(state["call_id"])
                return routed, _timings(
                    state, interpretation_ms=routed.llm_ms, interpretation_origin="classifier"
                )
        # ¿Ya se lanzó esta interpretación sobre el transcript parcial? Se reutiliza (o se espera
        # a que termine): solapa el LLM con la detección de fin de turno del STT.
        key = speculation_key(utterance)
        own = deps.speculations.pop(state["call_id"], {})
        future = own.pop(key, None)
        if future is None and own:
            # El parcial y el final suelen diferir en una palabra o en un número en letra:
            # se acepta la especulación más parecida si supera el 90 % de similitud
            best, score = max(
                (
                    (k, SequenceMatcher(None, _normalize(k), _normalize(key)).ratio())
                    for k in own
                ),
                key=lambda p: p[1],
            )
            # Con menos parecido solo vale si los datos "duros" (números y letras sueltas:
            # pisos, puertas, portales) son idénticos en el parcial y en el final
            duros = re.compile(r"\b(?:\d+|[a-z])\b")
            same_hard_tokens = duros.findall(_normalize(best)) == duros.findall(_normalize(key))
            if score >= 0.9 or (score >= 0.8 and same_hard_tokens):
                future = own.pop(best)
        for other in own.values():
            other.cancel()
        if future is not None and not future.cancelled():
            t0 = perf_counter()
            try:
                interp = await future
                return interp, _timings(
                    state,
                    interpretation_ms=int((perf_counter() - t0) * 1000),
                    llm_calls=1,
                    interpretation_origin="llm_speculative",
                )
            except Exception:  # noqa: BLE001 - la especulación falló: interpretación normal
                logger.warning("Especulación fallida en %s; se interpreta de nuevo", node)
        interp = await interpret(
            deps.llm,
            state.get("last_question"),
            utterance,
            node,
            catalog=_catalog(state),
            heard_text=heard_text_if_cut(state.get("last_delivery")),
            equipment_list=equipment_for(state),
            known=known_context(state),
        )
        return interp, _timings(
            state, interpretation_ms=interp.llm_ms, llm_calls=1, interpretation_origin="llm"
        )

    def _interp_view(interp: Interpretation) -> dict[str, Any]:
        return {
            # Without the name: the interpretation is kept in the state and the turn record
            **interp.model_dump(exclude_none=True, exclude={"name"}),
            "origin": interp.origin,
            "llm_ms": interp.llm_ms,
        }

    def _global_intent(
        state: CallState,
        node: str,
        interp: Interpretation,
        history: list[dict[str, str]],
        extra: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atiende repetir/pregunta/fuera_ambito/humano/colgar. None si el turno sigue su curso."""
        last = state.get("last_question") or _phrases().default_question
        provides_data = bool(interp.present_fields()) or interp.confirms is not None
        if interp.intent == "repeat":
            # Se repite el mensaje completo (resumen incluido), no solo la pregunta final
            full = _without_repeat_tag(state.get("last_bot_message") or last)
            return _ask(
                state,
                node,
                render("global.repeat", message=full),
                history=history,
                trace=_with_trace(state, node, "intent", "repeat"),
                **extra,
            )
        if (interp.intent == "question" or interp.question) and not provides_data:
            return _ask(
                state,
                node,
                _answer_question(interp.question or state.get("user_utterance") or "")
                + last,
                history=history,
                trace=_with_trace(state, node, "intent", "question"),
                **extra,
            )
        if interp.intent == "out_of_scope" and not provides_data:
            return _ask(
                state,
                node,
                render("global.out_of_scope", question=last),
                history=history,
                trace=_with_trace(state, node, "intent", "out_of_scope"),
                **extra,
            )
        # Derivar o colgar son irreversibles: exigen palabras explícitas del vecino, no solo la
        # opinión del modelo (una dirección mal transcrita llegó a derivarse como "humano").
        utterance = state.get("user_utterance") or ""
        if interp.intent == "human" and _asks_for_person(utterance):
            return _advance(
                state,
                "handoff_to_human",
                history=history,
                trace=_with_trace(state, node, "intent", "human"),
                **extra,
            )
        if interp.intent == "hang_up" and not provides_data and _says_goodbye(utterance):
            return _advance(
                state,
                "farewell",
                history=history,
                trace=_with_trace(state, node, "intent", "hang_up"),
                **extra,
            )
        return None

    async def _turn(
        state: CallState, node: str, *, yes_no: bool = False
    ) -> tuple[Interpretation, list[dict[str, str]], dict[str, Any], dict[str, Any] | None]:
        """Registra la entrada, interpreta y atiende intenciones globales.

        Devuelve (interp, historial, extra_estado, salida_global_o_None)."""
        utterance = state["user_utterance"] or ""
        history = _record_utterance(state)
        if _did_not_hear(utterance, state):
            full = _without_repeat_tag(
                state.get("last_bot_message") or state.get("last_question") or ""
            )
            repeat_output = _ask(
                state,
                node,
                render("global.not_heard", message=full),
                history=history,
                turn_timings=_timings(state, interpretation_origin="deterministic"),
                interpretation={"intent": "repeat", "origin": "deterministic", "llm_ms": 0},
                trace=_with_trace(state, node, "repeat_not_heard", utterance),
            )
            repeat_output["last_question"] = state.get("last_question")
            return (
                Interpretation(intent="repeat", origin="deterministic"),
                history,
                {},
                repeat_output,
            )
        if _is_filler(utterance):
            filler_output = _ask(
                state,
                node,
                render("global.filler"),
                history=history,
                turn_timings=_timings(state, interpretation_origin="deterministic"),
                interpretation={"intent": "filler", "origin": "deterministic", "llm_ms": 0},
                trace=_with_trace(state, node, "filler", utterance),
            )
            filler_output["last_question"] = state.get("last_question")
            return Interpretation(origin="deterministic"), history, {}, filler_output
        interp, timings = await _interpret(state, utterance, node, yes_no=yes_no)
        extra = {"turn_timings": timings, "interpretation": _interp_view(interp)}
        missed_what_it_confirms = interp.confirms is not None and invalid_confirmation(
            state.get("last_delivery")
        )
        if missed_what_it_confirms and interp.present_fields():
            # "Sí, sí, y además…" sobre un resumen que no oyó: los datos valen, el sí no. El
            # aviso de lo oído en el prompt no basta, Gemini lo confirmaba igual (2026-10-21)
            interp = interp.model_copy(update={"confirms": None})
            extra["interpretation"] = _interp_view(interp)
        elif missed_what_it_confirms:
            # Un sí/no suelto a algo que no oyó (cortó el resumen o la pregunta): se repite
            full = _without_repeat_tag(
                state.get("last_bot_message") or state.get("last_question") or ""
            )
            repeat_output = _ask(
                state,
                node,
                render("global.not_heard", message=full),
                history=history,
                trace=_with_trace(state, node, "confirmation_unheard", utterance),
                **extra,
            )
            repeat_output["last_question"] = state.get("last_question")
            return interp, history, extra, repeat_output
        output = _global_intent(state, node, interp, history, extra)
        return interp, history, extra, output

    # ------------------------------------------------------------------ enrutador determinista

    async def next_step(state: CallState) -> dict[str, Any]:
        """Decide el siguiente nodo según lo que falta, consumiendo lo que el vecino ya dijo."""
        node = "next_step"
        queued = dict(state.get("pending", {}))
        changes: dict[str, Any] = {}
        trace = list(state.get("trace", []))
        validations = list(state.get("validations", []))

        if not state.get("estate_id"):
            address = queued.pop("address", None)
            if address:
                estates = await deps.repo.list_estates()
                scored = sorted(
                    ((_address_similarity(address, f.address), f) for f in estates),
                    key=lambda p: p[0],
                    reverse=True,
                )
                if scored and scored[0][0] >= ESTATE_THRESHOLD:
                    score, estate = scored[0]
                    trace.append(
                        Trace(
                            node=node,
                            action="candidate",
                            detail=f"{estate.address} ({score:.2f})",
                        )
                    )
                    return _ask(
                        state,
                        "identify_estate",
                        render("identify_estate.candidate", address=estate.address),
                        candidate_estate_id=estate.id,
                        pending=queued,
                        trace=trace,
                    )
                return _retry_estate(
                    state,
                    render("identify_estate.reason_not_found"),
                    queued,
                    trace,
                )
            return _advance(state, "identify_estate", pending=queued, trace=trace)

        if not state.get("home_id"):
            floor, door = queued.get("floor"), queued.get("door")
            if floor and door:
                entrance = _normalize_entrance(queued.get("entrance"))
                nf_, nd = _normalize_floor(floor), _normalize_door(door)
                homes = await deps.repo.homes_of(state["estate_id"])  # type: ignore[arg-type]
                if entrance and not any(v.entrance for v in homes):
                    entrance = None  # the building has no entrances: the "entrance" is noise
                candidates = _home_candidates(homes, entrance, nf_, nd)
                trace.append(
                    Trace(
                        node=node,
                        action="home",
                        detail=f"entrance={entrance} floor={nf_} door={nd} candidates={len(candidates)}",
                    )
                )
                if len(candidates) == 1:
                    home = candidates[0]
                    for k in ("entrance", "floor", "door"):
                        queued.pop(k, None)
                    changes.update(
                        home_id=home.id, home_label=_home_label(home), home_parts=_home_parts(home)
                    )
                    validations.append("home_exists_in_estate")
                    state = {**state, **changes}  # type: ignore[typeddict-item]
                elif len(candidates) > 1:
                    # Mismo piso y puerta en varios portales: solo falta el portal (no es un fallo)
                    queued.pop("entrance", None)
                    entrances = sorted({v.entrance or "" for v in candidates})
                    return _ask(
                        state,
                        "identify_home",
                        render(
                            "identify_home.several_entrances",
                            floor=nf_,
                            door=nd,
                            entrances=_p().join_or(entrances),
                        ),
                        pending=queued,
                        trace=trace,
                        **changes,
                    )
                else:
                    queued.pop("door", None)
                    if not any(_normalize_floor(v.floor) == nf_ for v in homes):
                        queued.pop("floor", None)
                        queued.pop("entrance", None)
                    return await _retry_home(state, queued, trace)
            elif floor or door or queued.get("entrance"):
                # Dato parcial ("el tercero", "portal 1"): se conserva y se pide solo lo que falta.
                # Si el vecino repite lo mismo sin avanzar, sí consume intento (evita bucles).
                previous = state.get("pending", {})
                no_progress = (
                    all(previous.get(k) == queued.get(k) for k in ("entrance", "floor", "door"))
                    and state.get("user_utterance") is not None
                )
                if no_progress:
                    return await _retry_home(state, queued, trace)
                falta = _p().missing_home_part(floor, door)
                has = _p().given_home_parts(
                    (
                        ("entrance", queued.get("entrance")),
                        ("floor", _normalize_floor(floor) if floor else None),
                        ("door", _normalize_door(door) if door else None),
                    )
                )
                return _ask(
                    state,
                    "identify_home",
                    render("identify_home.partial", has=has, falta=falta),
                    pending=queued,
                    trace=[*trace, Trace(node=node, action="partial_home", detail=has)],
                    **changes,
                )
            else:
                return _advance(
                    state, "identify_home", pending=queued, trace=trace, **changes
                )

        if not state.get("home_confirmed") and not _is_emergency(state, queued):
            # Building and apartment are read back together before going on, so a misheard
            # floor or letter is caught here and not after the whole report (an emergency
            # skips it: the safety instruction comes first)
            return _advance(
                state,
                "confirm_home",
                pending=queued,
                trace=trace,
                validations=validations,
                **changes,
            )

        if not state.get("request"):
            problem = queued.pop("problem", None)
            if problem:
                changes["request"] = problem
                for k in ("category", "urgency", "summary"):
                    v = queued.pop(k, None)
                    if v:
                        changes["declared_urgency" if k == "urgency" else k] = v
                changes["work_type"] = queued.pop("work_type", None)
                changes["equipment_id"] = queued.pop("equipment", None)
                changes["equipment_evaluated"] = bool(queued.pop("equipment_evaluated", None))
                if changes.get("category"):
                    # El resumen corto lo da la interpretación; si falta, vale la propia solicitud
                    changes.setdefault("summary", queued.pop("short_problem", None) or problem)
                    changes["confidence_signals"] = {
                        "model": deps.llm.name,
                        "prompt_version": interpretation_version(),
                        "category_in_catalog": changes["category"] in _catalog(state),
                        "merged_with_interpretation": True,
                    }
                trace.append(Trace(node=node, action="request_collected", detail=problem[:60]))
                state = {**state, **changes}  # type: ignore[typeddict-item]
            else:
                return _advance(
                    state,
                    "collect_request",
                    pending=queued,
                    trace=trace,
                    validations=validations,
                    **changes,
                )

        if not state.get("category") or (
            # La avería se contó antes de confirmar la finca: se clasifica otra vez con sus equipos
            state.get("equipment_list") and not state.get("equipment_evaluated")
        ):
            target = "classify_problem"
        elif not state.get("urgency"):
            target = "determine_urgency"
        elif state.get("urgency") == "emergency":
            # Solo llega aquí con `emergencies_enabled` (si no, `determine_urgency` la deja en
            # alta): ni detalle técnico ni resumen largo, primero la indicación de seguridad
            target = "validate_data" if state.get("emergency_handled") else "handle_emergency"
        elif (
            not state.get("detail_requested")
            and _config(state).ask_detail
            and state.get("confirmed") is None
            and _deserves_detail(state)
        ):
            target = "ask_detail"
        elif state.get("confirmed") is not True:
            target = "confirm_summary"
        else:
            target = "validate_data"
        return _advance(
            state, target, pending=queued, trace=trace, validations=validations, **changes
        )

    def _retry_estate(
        state: CallState,
        prefix: str,
        queued: dict[str, str],
        trace: list[Trace],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = state.get("estate_attempts", 0) + 1
        if attempts >= DEFAULTS.estate_attempts:
            return _advance(
                state,
                "estate_not_identified",
                estate_attempts=attempts,
                candidate_estate_id=None,
                pending=queued,
                trace=[
                    *trace,
                    Trace(
                        node="identify_estate", action="attempts_exhausted", detail=str(attempts)
                    ),
                ],
                **(extra or {}),
            )
        return _ask(
            state,
            "identify_estate",
            render("identify_estate.retry", reason=prefix),
            estate_attempts=attempts,
            candidate_estate_id=None,
            pending=queued,
            trace=[
                *trace,
                Trace(node="identify_estate", action="retry", detail=str(attempts)),
            ],
            **(extra or {}),
        )

    async def _describe_homes(estate_id: str) -> str:
        """'pisos del 1 al 4 y puertas A o B' a partir de las viviendas reales de la finca."""
        homes = await deps.repo.homes_of(estate_id)
        if not homes:
            return ""
        floors = sorted(
            {_normalize_floor(v.floor) or v.floor for v in homes},
            key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x),
        )
        doors = sorted({v.door for v in homes})
        entrances = sorted({v.entrance for v in homes if v.entrance})
        return _p().describe_homes(entrances, floors, doors)

    async def _ask_floor_door(state: CallState, queued: dict[str, str]) -> str | None:
        """Con un piso que existe pero sin puerta válida: '¿la A o la B?' en vez de empezar de cero."""
        floor = _normalize_floor(queued.get("floor"))
        if not (floor and state.get("estate_id")) or queued.get("door"):
            return None
        homes = await deps.repo.homes_of(state["estate_id"])  # type: ignore[arg-type]
        entrance = _normalize_entrance(queued.get("entrance"))
        doors = sorted(
            {
                v.door
                for v in homes
                if _normalize_floor(v.floor) == floor
                and (not entrance or _normalize_entrance(v.entrance) == entrance)
            }
        )
        if not doors:
            return None
        return render(
            "identify_home.floor_door",
            floor=_ordinal(floor),
            doors=_p().join_or(doors),
        )

    async def _retry_home(
        state: CallState,
        queued: dict[str, str],
        trace: list[Trace],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = state.get("home_attempts", 0) + 1
        if attempts >= _config(state).home_attempts:
            return _advance(
                state,
                "home_not_identified",
                home_attempts=attempts,
                pending=queued,
                trace=trace,
                **(extra or {}),
            )
        text = await _ask_floor_door(state, queued)
        if text is None:
            estate_id = state.get("estate_id")
            hint = await _describe_homes(estate_id) if estate_id else ""
            text = render("identify_home.retry", hint=hint)
        return _ask(
            state,
            "identify_home",
            text,
            home_attempts=attempts,
            pending=queued,
            trace=trace,
            **(extra or {}),
        )

    def _home_no_progress(
        state: CallState,
        history: list[dict[str, str]],
        queued: dict[str, str],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """The apartment question gets no new data: repeat it, using up an attempt."""
        attempts = state.get("home_attempts", 0) + 1
        if attempts >= _config(state).home_attempts:
            return _advance(
                state,
                "home_not_identified",
                history=history,
                home_attempts=attempts,
                pending=queued,
                trace=_with_trace(state, "identify_home", "attempts_exhausted", str(attempts)),
                **extra,
            )
        return _ask(
            state,
            "identify_home",
            state.get("last_question") or render("identify_home.reask"),
            history=history,
            home_attempts=attempts,
            pending=queued,
            trace=_with_trace(state, "identify_home", "no_progress", str(attempts)),
            **extra,
        )

    async def _estate_matches(address: str) -> bool:
        estates = await deps.repo.list_estates()
        return any(_address_similarity(address, f.address) >= ESTATE_THRESHOLD for f in estates)

    async def _corrected_home(
        state: CallState, interp: Interpretation, utterance: str, *, by_format: bool = False
    ) -> dict[str, str] | None:
        """The full apartment after a correction, or None if the turn corrects no apartment.

        The model often returns only the part it thinks changed ("I meant the 3B" -> door B):
        a "3B" said in so many words wins over it, and whatever was not corrected stays as in
        the apartment on file ("it's B, not A" keeps the floor), so the bot never asks again
        for a floor the caller has just said. `by_format`: a turn with no apartment from the
        model still counts if it has one by format (used where a short "no" skips the model).
        """
        fields = {
            k: str(getattr(interp, k)) for k in ("entrance", "floor", "door") if getattr(interp, k)
        }
        if not fields and not by_format:
            return None
        if not ("floor" in fields and "door" in fields):
            # Only when the model gave part of it: with the whole apartment the model wins
            # ("earlier I said 3B but it's 3A" names two)
            entrance, floor, door = _extract_home_regex(utterance)
            if floor and door:
                fields.update(floor=floor, door=door)
                if entrance:
                    fields["entrance"] = entrance
            elif fields:
                for k, v in (("entrance", entrance), ("floor", floor), ("door", door)):
                    if v and k not in fields:
                        fields[k] = v
            # With nothing from the model, a lone floor by format is not a correction: problems
            # name floors too ("no funciona la luz del pasillo del segundo")
        if not fields:
            return None
        if state.get("home_id") and state.get("estate_id"):
            homes = await deps.repo.homes_of(state["estate_id"])  # type: ignore[arg-type]
            current = next((h for h in homes if h.id == state["home_id"]), None)
            if current is not None:
                for k in ("entrance", "floor", "door"):
                    if k not in fields and getattr(current, k):
                        fields[k] = str(getattr(current, k))
        return fields

    # ------------------------------------------------------------------ nodos que preguntan

    async def identify_estate(state: CallState) -> dict[str, Any]:
        node = "identify_estate"
        utterance = state.get("user_utterance")
        if utterance is None:
            return _ask(
                state,
                node,
                render("identify_estate.greeting"),
            )
        candidate = state.get("candidate_estate_id")
        if candidate:
            interp, history, extra, output = await _turn(state, node, yes_no=True)
            if output:
                return output
            estate = await deps.repo.get_estate(candidate)
            assert estate is not None
            # "Yes, 12 Main Street, yes": repeating the building just read back is a yes, not
            # another address (otherwise the same question is asked again)
            repeats_it = bool(interp.address) and (
                _address_similarity(interp.address or "", estate.address) >= ESTATE_THRESHOLD
            )
            if repeats_it:
                interp = interp.model_copy(update={"address": None})
            if not (interp.floor or interp.door):
                # "The home is in the 2A" sometimes comes back without the apartment (1 in 3,
                # eval 2026-11-16): a whole apartment by format is taken. Floor and door both,
                # so the number of an address ("12 Main Street") never passes for a floor
                entrance, floor, door = _extract_home_regex(state.get("user_utterance") or "")
                if floor and door:
                    interp = interp.model_copy(
                        update={"entrance": entrance, "floor": floor, "door": door}
                    )
            # Answering the building question with the apartment ("it's 3A") moves the call
            # forward: the caller accepts the building implicitly. The model sometimes marks it
            # as a "no" because it is not the yes/no that was asked, so it is decided here.
            implicit_yes = (
                (not interp.address and bool(interp.floor or interp.door))
                or (repeats_it and interp.confirms is not False)
                or _moves_on_to_problem(interp, state.get("user_utterance") or "")
            )
            if (interp.confirms is True or implicit_yes) and not interp.address:
                cfg, cfg_id = await deps.repo.effective_config(estate.id)
                equipment_list = [
                    {**e.model_dump(mode="json"), "description": e.description()}
                    for e in await deps.repo.equipment_of(estate.id)
                ]
                return _advance(
                    state,
                    "next_step",
                    history=history,
                    estate_id=estate.id,
                    estate_address=estate.address,
                    candidate_estate_id=None,
                    config=cfg.model_dump(),
                    config_id=cfg_id,
                    equipment_list=equipment_list,
                    pending=_merge_pending(state, interp),
                    validations=[*state.get("validations", []), "estate_confirmed_verbally"],
                    trace=_with_trace(state, node, "estate_confirmed", estate.address),
                    **extra,
                )
            if interp.address:
                # Rechaza la candidata aportando otra dirección: se evalúa sin gastar intento extra
                return _advance(
                    state,
                    "next_step",
                    history=history,
                    candidate_estate_id=None,
                    pending=_merge_pending(state, interp),
                    trace=_with_trace(state, node, "candidate_rejected", "new address"),
                    **extra,
                )
            if interp.confirms is False:
                return _retry_estate(
                    {**state, "history": history},
                    render("identify_estate.reason_not_that_one"),
                    _merge_pending(state, interp),
                    state.get("trace", []),
                    extra,
                )
            return _ask(
                state,
                node,
                render("identify_estate.confirm_yes_no"),
                history=history,
                **extra,
            )

        # Sin candidata: primero coincidencia determinista de la frase completa (0 llamadas LLM)
        history = _record_utterance(state)
        if _is_greeting(utterance):
            # "Hola, buenas": se devuelve el saludo y se repite la pregunta sin gastar intento
            return _ask(
                state,
                node,
                render("identify_estate.greeting_reply"),
                history=history,
                turn_timings=_timings(state, interpretation_origin="deterministic"),
                interpretation={"intent": "greeting", "origin": "deterministic", "llm_ms": 0},
                trace=_with_trace(state, node, "greeting", utterance),
            )
        if _yes_no_answer(utterance) is not None and len(utterance.split()) <= 3:
            # "Sí."/"No." no es una dirección: mismo reintento que daría el modelo, sin esperarlo
            return _retry_estate(
                {**state, "history": history},
                render("identify_estate.reason_not_understood"),
                dict(state.get("pending", {})),
                state.get("trace", []),
                {
                    "turn_timings": _timings(state, interpretation_origin="deterministic"),
                    "interpretation": {"origin": "deterministic", "llm_ms": 0},
                },
            )
        estates = await deps.repo.list_estates()
        scored = sorted(
            ((_address_similarity(utterance, f.address), f) for f in estates),
            key=lambda p: p[0],
            reverse=True,
        )
        if (
            scored
            and scored[0][0] >= ESTATE_THRESHOLD
            and len(utterance.split()) <= 8
            and _only_address(utterance, scored[0][1].address)
        ):
            score, estate = scored[0]
            return _ask(
                state,
                node,
                render("identify_estate.candidate", address=estate.address),
                history=history,
                candidate_estate_id=estate.id,
                turn_timings=_timings(state, interpretation_origin="deterministic"),
                interpretation={"address": utterance, "origin": "deterministic", "llm_ms": 0},
                trace=_with_trace(
                    state, node, "candidate", f"{estate.address} ({score:.2f})"
                ),
            )
        interp, history, extra, output = await _turn(state, node)
        if output:
            return output
        queued = _merge_pending(state, interp)
        if not interp.address:
            return _retry_estate(
                {**state, "history": history},
                render("identify_estate.reason_not_understood"),
                queued,
                state.get("trace", []),
                extra,
            )
        return _advance(state, "next_step", history=history, pending=queued, **extra)

    async def estate_not_identified(state: CallState) -> dict[str, Any]:
        node = "estate_not_identified"
        utterance = state.get("user_utterance")
        if utterance is None:
            return _ask(
                state,
                node,
                render("estate_not_identified.collect"),
            )
        history = _record_utterance(state)
        call = _call_from(
            {**state, "history": history, "request": utterance}, "estate_not_identified"
        )
        await deps.repo.save_call(call)
        return _advance(
            state,
            "end",
            render("estate_not_identified.farewell"),
            history=history,
            request=utterance,
            result="estate_not_identified",
            finished=True,
            trace=_with_trace(state, node, "call_logged", "no technical issue"),
        )

    async def identify_home(state: CallState) -> dict[str, Any]:
        node = "identify_home"
        utterance = state.get("user_utterance")
        if utterance is None:
            return _ask(
                state,
                node,
                render("identify_home.ask"),
            )
        # Un "sí"/"no" suelto con datos parciales pendientes: no hay nada que extraer, se repite
        # la pregunta pendiente (el STT transcribe "sí" como "S.")
        previous_pending = state.get("pending", {})
        if (
            _yes_no_answer(utterance) is not None
            and len(utterance.split()) <= 3
            and (previous_pending.get("floor") or previous_pending.get("door") or previous_pending.get("entrance"))
        ):
            return _home_no_progress(
                state,
                _record_utterance(state),
                dict(previous_pending),
                {
                    "turn_timings": _timings(state, interpretation_origin="deterministic"),
                    "interpretation": {"confirms": True, "origin": "deterministic", "llm_ms": 0},
                },
            )
        # Formato corto y reconocible: sin LLM (regla: regex solo para formatos)
        entrance, floor, door = _extract_home_regex(utterance)
        if floor and door and len(utterance.split()) <= 5:
            queued = {**state.get("pending", {}), "floor": floor, "door": door}
            if entrance:
                queued["entrance"] = entrance
            return _advance(
                state,
                "next_step",
                history=_record_utterance(state),
                pending=queued,
                turn_timings=_timings(state, interpretation_origin="deterministic"),
                interpretation={
                    "entrance": entrance,
                    "floor": floor,
                    "door": door,
                    "origin": "deterministic",
                    "llm_ms": 0,
                },
                trace=_with_trace(
                    state, node, "regex_extraction", f"entrance={entrance} floor={floor} door={door}"
                ),
            )
        interp, history, extra, output = await _turn(state, node)
        if output:
            return output
        queued = _merge_pending(state, interp)
        # El LLM es la primera fuente de comprensión; el regex solo entra si lo que entendió el
        # modelo no corresponde a ninguna vivienda de la finca (p. ej. inventó portal="a").
        if floor and door and state.get("estate_id"):
            homes = await deps.repo.homes_of(state["estate_id"])  # type: ignore[arg-type]
            llm_entrance = _normalize_entrance(queued.get("entrance"))
            if llm_entrance and not any(v.entrance for v in homes):
                llm_entrance = None
            from_llm = _home_candidates(
                homes,
                llm_entrance,
                _normalize_floor(queued.get("floor")),
                _normalize_door(queued.get("door")),
            )
            if not from_llm and _home_candidates(homes, entrance, floor, door):
                queued["floor"], queued["door"] = floor, door
                if entrance:
                    queued["entrance"] = entrance
                else:
                    queued.pop("entrance", None)
            elif not from_llm:
                # Lo del modelo no existe en la finca; nos quedamos con lo que el regex sí vio
                for k, v in (("entrance", entrance), ("floor", floor), ("door", door)):
                    if v:
                        queued[k] = v
                    else:
                        queued.pop(k, None)
        # El regex completa los campos de formato que el modelo no dio ("puerta A" tras "el tercero")
        for k, v in (("entrance", entrance), ("floor", floor), ("door", door)):
            if v and not queued.get(k):
                queued[k] = v
        said_home = any((interp.entrance, interp.floor, interp.door, entrance, floor, door))
        if not said_home and any(previous_pending.get(k) for k in ("entrance", "floor", "door")):
            # Mid-question ("which entrance, 1 or 2?") with nothing about the apartment in the
            # answer: the question is asked again, but it uses up an attempt (no endless loop);
            # whatever else was said, like the problem, stays pending
            return _home_no_progress({**state, "history": history}, history, queued, extra)
        if not (queued.get("floor") and queued.get("door")):
            # Sin piso ni puerta ni portal: no ha dado ningún dato de vivienda -> reintento.
            # Con algún dato parcial, `next_step` lo conserva y pide solo lo que falta.
            if not (queued.get("floor") or queued.get("door") or queued.get("entrance")):
                return await _retry_home(
                    {**state, "history": history},
                    queued,
                    state.get("trace", []),
                    extra,
                )
        return _advance(state, "next_step", history=history, pending=queued, **extra)

    async def home_not_identified(state: CallState) -> dict[str, Any]:
        node = "home_not_identified"
        utterance = state.get("user_utterance")
        already_said = state.get("request") or state.get("pending", {}).get("problem")
        if utterance is None and not already_said:
            return _ask(
                state,
                node,
                render("home_not_identified.collect"),
            )
        if utterance is None:
            # The problem was already told while the apartment was being asked: it is not
            # asked for again
            history = list(state.get("history", []))
            request = str(already_said)
            message = render("home_not_identified.noted")
        else:
            history = _record_utterance(state)
            request = utterance
            message = render("home_not_identified.farewell")
        cfg = _config(state)
        target = cfg.contact.email if cfg.contact else "administrator"
        call = _call_from(
            {**state, "history": history, "request": request}, "home_not_identified"
        )
        await deps.repo.save_call(call)
        return _advance(
            state,
            "end",
            message,
            history=history,
            request=request,
            result="home_not_identified",
            finished=True,
            notice_channel="email",
            notice_target=target,
            trace=_with_trace(
                state, node, "simulated_email", f"summary to the property manager -> {target}"
            ),
        )

    async def confirm_home(state: CallState) -> dict[str, Any]:
        node = "confirm_home"
        utterance = state.get("user_utterance")
        values = {
            "address": state.get("estate_address") or "",
            "home": _spoken_home(state),
        }
        if utterance is None:
            return _ask(state, node, render("confirm_home.ask", **values))
        interp, history, extra, output = await _turn(state, node, yes_no=True)
        if output:
            return output
        queued = _merge_pending(state, interp)
        # A short "No, it's 3B" is decided as a plain "no" without the model, so the apartment
        # in it is also read by format here
        corrected = await _corrected_home(state, interp, utterance, by_format=True)
        if corrected:
            # "No, it's 3B": next_step looks the new apartment up and it is read back again
            queued.update(corrected)
            return _advance(
                state,
                "next_step",
                history=history,
                pending=queued,
                home_id=None,
                home_label=None,
                home_parts=None,
                home_confirmed=None,
                trace=_with_trace(state, node, "correction", "apartment"),
                **extra,
            )
        if (
            interp.address
            and interp.confirms is not True
            and await _estate_matches(interp.address)
        ):
            return _advance(
                state,
                "next_step",
                history=history,
                pending=queued,
                estate_id=None,
                estate_address=None,
                home_id=None,
                home_label=None,
                home_parts=None,
                home_confirmed=None,
                trace=_with_trace(state, node, "correction", "building"),
                **extra,
            )
        if interp.confirms is True or _moves_on_to_problem(interp, utterance):
            # Going straight to the problem ("the elevator is stuck") accepts the read-back
            return _advance(
                state,
                "next_step",
                history=history,
                pending=queued,
                home_confirmed=True,
                validations=[*state.get("validations", []), "home_confirmed_verbally"],
                trace=_with_trace(state, node, "home_confirmed", state.get("home_label") or ""),
                **extra,
            )
        if interp.confirms is False:
            for k in ("entrance", "floor", "door"):
                queued.pop(k, None)
            return _ask(
                state,
                "identify_home",
                render("confirm_home.wrong"),
                history=history,
                pending=queued,
                home_id=None,
                home_label=None,
                home_parts=None,
                home_confirmed=None,
                trace=_with_trace(state, node, "home_rejected", state.get("home_label") or ""),
                **extra,
            )
        return _ask(
            state,
            node,
            render("confirm_home.yes_no", **values),
            history=history,
            pending=queued,
            **extra,
        )

    async def collect_request(state: CallState) -> dict[str, Any]:
        node = "collect_request"
        utterance = state.get("user_utterance")
        if utterance is None:
            greeting = render("collect_request.greeting")
            return _ask(state, node, render("collect_request.ask", greeting=greeting))
        interp, history, extra, output = await _turn(state, node)
        if output:
            return output
        queued = _merge_pending(state, interp)
        if not interp.problem:
            corrected = await _corrected_home(state, interp, utterance)
            if corrected:
                # The caller corrects the apartment instead of describing the problem: update
                # the apartment and ask for the problem again (never file it as the request)
                queued.pop("problem", None)
                queued.update(corrected)
                return _advance(
                    state,
                    "next_step",
                    history=history,
                    pending=queued,
                    confirmed=None,
                    home_id=None,
                    home_label=None,
                    home_parts=None,
                    home_confirmed=None,
                    trace=_with_trace(state, node, "correction", "apartment"),
                    **extra,
                )
            if interp.confirms is not None or _is_filler(utterance):
                # A bare "yes", "okay" or filler is not a problem description
                queued.pop("problem", None)
                return _ask(
                    state,
                    node,
                    render("collect_request.retry"),
                    history=history,
                    pending=queued,
                    **extra,
                )
        # La descripción literal manda; el resumen va aparte
        queued["problem"] = utterance.strip()
        return _advance(
            state, "next_step", history=history, pending=queued, confirmed=None, **extra
        )

    async def classify_problem(state: CallState) -> dict[str, Any]:
        node = "classify_problem"
        catalog = _catalog(state)
        validations = list(state.get("validations", []))
        category = state.get("category")
        urgency = state.get("declared_urgency")
        summary = state.get("summary")
        timings = dict(state.get("turn_timings", {}))
        signals = dict(state.get("confidence_signals") or {})
        work_type, equipment_id = state.get("work_type"), state.get("equipment_id")
        equipment_list = equipment_for(state)
        routed = (
            await asyncio.to_thread(deps.router.classify, state.get("request") or "", catalog)
            if deps.router is not None and not category and not equipment_list
            else None
        )
        if routed is not None:
            category, urgency, work_type = routed.category, routed.urgency, routed.work_type
            timings = _timings(state, classification_ms=routed.ms)
            signals = {
                "model": deps.router.name if deps.router else "",
                "prompt_version": None,
                "valid_urgency": urgency in URGENCIES,
            }
        elif not category or (equipment_list and not state.get("equipment_evaluated")):
            t0 = perf_counter()
            extracted = wire.read(
                await deps.llm.extract(
                    classification_system_prompt(catalog, equipment_list),
                    state.get("request") or "",
                    classification_schema(catalog),
                )
            )
            work_type = extracted.get("work_type")
            ids = {i for i, _ in equipment_list or []}
            equipment_id = extracted.get("equipment") if extracted.get("equipment") in ids else None
            ms = int((perf_counter() - t0) * 1000)
            timings = _timings(state, classification_ms=ms, llm_calls=1)
            category, urgency, summary = (
                extracted.get("category"),
                extracted.get("urgency"),
                extracted.get("summary"),
            )
            signals = {
                "model": deps.llm.name,
                "prompt_version": classification_version(),
                "valid_urgency": urgency in URGENCIES,
            }
        if category not in catalog:
            validations.append("category_out_of_catalog->unclassifiable")
            category = UNCLASSIFIABLE_CATEGORY
        else:
            validations.append("category_in_catalog")
        if urgency not in URGENCIES:
            validations.append("invalid_urgency->medium")
            urgency = "medium"
        signals["category_in_catalog"] = category != UNCLASSIFIABLE_CATEGORY
        return _advance(
            state,
            "determine_urgency",
            work_type=work_type if work_type in WORK_TYPES else "repair",
            equipment_id=equipment_id,
            equipment_evaluated=True,
            category=category,
            declared_urgency=urgency,
            summary=(summary or state.get("request") or "").strip(),
            confidence_signals=signals,
            validations=validations,
            turn_timings=timings,
            trace=_with_trace(state, node, "classified", f"{category} / {urgency}"),
        )

    def _deserves_detail(state: CallState) -> bool:
        """Una descripción corta se beneficia de una pregunta de detalle; una larga no. Si no es
        una reparación (vandalismo, obra...), el detalle técnico lo valora el administrador."""
        if (state.get("work_type") or "repair") != "repair":
            return False
        request = state.get("request") or ""
        return len(request.split()) < 18

    async def ask_detail(state: CallState) -> dict[str, Any]:
        """Un solo turno para concretar la incidencia (dónde, cuántos, desde cuándo, cómo falla)."""
        node = "ask_detail"
        utterance = state.get("user_utterance")
        if utterance is None:
            equipment = next(
                (e for e in state.get("equipment_list", []) if e["id"] == state.get("equipment_id")), None
            )
            if equipment and equipment["kind"] == state.get("category"):
                # The elevator question (stopped or on and off, anyone inside?) fits the
                # building's elevator too, and does not ask "since when" again
                question = _p().detail_question(equipment["kind"])
            elif equipment:
                # La pregunta de la categoría ("para orientar al electricista") no sirve para un
                # portero o un ascensor concretos
                question = _phrases().equipment_detail.format(kind=equipment["kind"])
            else:
                question = _p().detail_question(state.get("category"))
            return _ask(state, node, question, detail_requested=True)
        interp, history, extra, output = await _turn(state, node)
        if output:
            return output
        detail = utterance.strip()
        if _yes_no_answer(utterance) is not None and len(utterance.split()) <= 3:
            detail = ""  # "no", "I don't know": no useful detail
        request = state.get("request") or ""
        if detail:
            request = f"{request} {_phrases().detail_prefix} {detail}"
        summary = state.get("summary") or ""
        if interp.problem and interp.problem.lower() not in summary.lower():
            if _phrases().lowercase_summary:
                # Read as one sentence: "the elevator is stuck, with people inside"
                summary = f"{summary}, {_mid_sentence(interp.problem)}" if summary else interp.problem
            else:
                summary = f"{summary}; {interp.problem}" if summary else interp.problem
        reclassify: dict[str, Any] = {}
        if detail and state.get("category") == UNCLASSIFIABLE_CATEGORY:
            # El detalle suele nombrar lo que falla: se clasifica de nuevo con la solicitud entera
            reclassify = {"category": None, "urgency": None, "declared_urgency": None}
        return _advance(
            state,
            "next_step",
            history=history,
            request=request,
            summary=summary,
            detail_requested=True,
            **reclassify,
            trace=_with_trace(state, node, "detail", detail[:60]),
            **extra,
        )

    async def determine_urgency(state: CallState) -> dict[str, Any]:
        node = "determine_urgency"
        cfg = _config(state)
        declared = state.get("declared_urgency") or "medium"
        effective = cfg.effective_urgency(declared)  # type: ignore[arg-type]
        detail = f"{declared} -> {effective}" if declared != effective else effective
        # Vuelve al enrutador: puede tocar pedir un detalle antes del resumen
        return _advance(
            state,
            "next_step",
            urgency=effective,
            trace=_with_trace(state, node, "effective_urgency", detail),
        )

    async def confirm_summary(state: CallState) -> dict[str, Any]:
        node = "confirm_summary"
        utterance = state.get("user_utterance")
        if utterance is None:
            # La categoría y la urgencia son internas: no se leen al vecino (2026-10-21).
            # Se siguen pasando para que una plantilla publicada que las use no se rompa.
            category = state.get("category") or ""
            # `who` stays empty: the caller's name is not kept. It is still passed so that a
            # published template that uses it does not break
            who = ""
            return _ask(
                state,
                node,
                render(
                    "confirm_summary.summary",
                    who=who,
                    summary=_mid_sentence((state.get("summary") or "").rstrip(". ")),
                    category=spoken_category(category),
                    urgency=state.get("urgency"),
                ),
            )
        interp, history, extra, output = await _turn(state, node, yes_no=True)
        if output:
            return output
        # Correcciones: el vecino rectifica datos en lugar de decir sí/no
        changes: dict[str, Any] = {}
        queued = dict(state.get("pending", {}))
        # Prioridad: problema > vivienda > finca. Una corrección del problema no toca la finca.
        if (
            interp.problem
            and interp.confirms is not True
            and _is_real_correction(interp.problem, state.get("request") or "", utterance)
        ):
            changes.update(
                request=None,
                category=None,
                urgency=None,
                declared_urgency=None,
                summary=None,
                confirmed=None,
                equipment_id=None,
                work_type=None,
            )
            queued["problem"] = utterance.strip()
            queued["short_problem"] = interp.problem
            for k in ("category", "urgency"):
                if getattr(interp, k):
                    queued[k] = getattr(interp, k)
        elif corrected := await _corrected_home(state, interp, utterance):
            changes.update(
                home_id=None,
                home_label=None,
                home_parts=None,
                home_confirmed=None,
                confirmed=None,
            )
            queued.update(corrected)
        elif (
            interp.address
            and interp.confirms is not True
            and await _estate_matches(interp.address)
        ):
            # Solo si la nueva dirección es una finca conocida: evita borrar la finca por un
            # "direccion" espurio del modelo ("es en el garaje")
            changes.update(
                estate_id=None,
                estate_address=None,
                home_id=None,
                home_label=None,
                home_parts=None,
                home_confirmed=None,
                confirmed=None,
            )
            queued["address"] = interp.address
        if changes:
            return _advance(
                state,
                "next_step",
                render("confirm_summary.correcting"),
                history=history,
                pending=queued,
                trace=_with_trace(state, node, "correction", ", ".join(sorted(queued))),
                **changes,
                **extra,
            )
        if interp.confirms is True:
            # "Sí, pero eso no lo arregla un electricista": los hechos valen, el reparto lo
            # decide el administrador
            return _advance(
                state,
                "validate_data",
                history=history,
                confirmed=True,
                objection=interp.objection,
                trace=_with_trace(
                    state,
                    node,
                    "confirmed_with_objection" if interp.objection else "confirmed",
                    interp.objection or "",
                ),
                **extra,
            )
        if interp.confirms is False:
            return _ask(
                state,
                "collect_request",
                render("confirm_summary.repeat_request"),
                history=history,
                confirmed=False,
                request=None,
                category=None,
                urgency=None,
                declared_urgency=None,
                summary=None,
                trace=_with_trace(state, node, "rejected", "back to collect_request"),
                **extra,
            )
        return _ask(
            state,
            node,
            render("confirm_summary.confirm_yes_no"),
            history=history,
            **extra,
        )

    async def handle_emergency(state: CallState) -> dict[str, Any]:
        """Emergencia con el tratamiento especial activado (Confirmado).

        Primero la indicación de seguridad y una confirmación mínima (finca y emergencia). Un
        "no" sin más la deja en urgencia alta y el flujo sigue normal; cualquier otra respuesta
        la registra: ante la duda, una emergencia no se pierde en un bucle de repreguntas."""
        node = "handle_emergency"
        utterance = state.get("user_utterance")
        if utterance is None:
            seguridad = _safety_instruction(
                f"{state.get('request') or ''} {state.get('summary') or ''}"
            )
            return _ask(
                state,
                node,
                render(
                    "handle_emergency.notice",
                    seguridad=seguridad.strip(),
                    address=state.get("estate_address") or _phrases().your_building,
                ),
                trace=_with_trace(
                    state,
                    node,
                    "template",
                    f"{getattr(seguridad, 'template_id', '')} v{getattr(seguridad, 'version', 0)}",
                ),
            )
        interp, history, extra, output = await _turn(state, node, yes_no=True)
        if output:
            return output
        if (
            interp.address
            and interp.confirms is not True
            and await _estate_matches(interp.address)
        ):
            # "No, es en la Alameda 5": otra finca. La urgencia se recalcula con su configuración
            return _advance(
                state,
                "next_step",
                history=history,
                estate_id=None,
                estate_address=None,
                home_id=None,
                home_label=None,
                home_parts=None,
                urgency=None,
                pending={**state.get("pending", {}), "address": interp.address},
                trace=_with_trace(state, node, "correction", "address"),
                **extra,
            )
        if interp.confirms is False:
            return _advance(
                state,
                "next_step",
                history=history,
                urgency="high",
                trace=_with_trace(state, node, "emergency_ruled_out", utterance[:60]),
                **extra,
            )
        request = state.get("request") or ""
        if not (_yes_no_answer(utterance) is not None and len(utterance.split()) <= 3):
            request = f"{request} {_phrases().detail_prefix} {utterance.strip()}"
        return _advance(
            state,
            "next_step",
            history=history,
            request=request,
            emergency_handled=True,
            confirmed=True if interp.confirms else state.get("confirmed"),
            trace=_with_trace(
                state,
                node,
                "emergency_confirmed" if interp.confirms else "emergency_unconfirmed",
                utterance[:60],
            ),
            **extra,
        )

    # ------------------------------------------------------------------ nodos de trabajo y cierre

    async def validate_data(state: CallState) -> dict[str, Any]:
        node = "validate_data"
        cfg = _config(state)
        validations = list(state.get("validations", []))
        problems: list[str] = []
        if not state.get("estate_id"):
            problems.append("no_estate")
        if not state.get("home_id"):
            problems.append("no_home")
        # Regla de diseño (2026-08-28): el bot sustituye al administrador. Con finca, vivienda
        # y solicitud confirmadas SIEMPRE se crea la incidencia; sin categoría o sin especialista
        # queda pendiente a nombre del administrador, nunca "escapa a lo que puedo gestionar".
        category = state.get("category")
        if category == UNCLASSIFIABLE_CATEGORY or category not in cfg.category_catalog:
            validations.append("no_category->pending_administrator")
        elif category not in cfg.specialist_by_category:
            validations.append("no_specialist->pending_administrator")
        if problems:
            validations.extend(f"failure:{p}" for p in problems)
            return _advance(
                state,
                "unclassifiable",
                validations=validations,
                review_reason=", ".join(problems),
                trace=_with_trace(state, node, "validation_failed", ", ".join(problems)),
            )
        validations.append("data_validated")
        return _advance(
            state,
            "assign_specialist",
            validations=validations,
            trace=_with_trace(state, node, "validation_passed"),
        )

    async def unclassifiable(state: CallState) -> dict[str, Any]:
        node = "unclassifiable"
        cfg = _config(state)
        target = cfg.contact.email if cfg.contact else "administrator"
        await deps.repo.save_call(
            _call_from(state, "unclassifiable", state.get("review_reason"))
        )
        return _advance(
            state,
            "end",
            render("unclassifiable.farewell"),
            result="unclassifiable",
            finished=True,
            notice_channel="email",
            notice_target=target,
            trace=_with_trace(
                state, node, "simulated_email", f"summary for review -> {target}"
            ),
        )

    async def handoff_to_human(state: CallState) -> dict[str, Any]:
        node = "handoff_to_human"
        cfg = _config(state)
        target = cfg.contact.email if cfg.contact else "administrator"
        await deps.repo.save_call(
            _call_from(state, "handed_to_administrator", "caller asked for a person")
        )
        return _advance(
            state,
            "end",
            render("handoff_to_human.farewell"),
            result="handed_to_administrator",
            finished=True,
            notice_channel="email",
            notice_target=target,
            trace=_with_trace(state, node, "simulated_email", f"handoff -> {target}"),
        )

    async def farewell(state: CallState) -> dict[str, Any]:
        node = "farewell"
        await deps.repo.save_call(_call_from(state, "abandoned"))
        return _advance(
            state,
            "end",
            render("farewell.farewell"),
            result="abandoned",
            finished=True,
            trace=_with_trace(state, node, "call_logged"),
        )

    async def assign_specialist(state: CallState) -> dict[str, Any]:
        """A quién va la incidencia (regla de diseño, 2026-10-21, Provisional).

        Se avisa directamente solo a una reparación con alguien que la atienda: primero el
        mantenedor del equipo afectado, si no el especialista de la categoría. Si no es una
        reparación o el vecino puso una pega, decide el administrador con una sugerencia."""
        node = "assign_specialist"
        cfg = _config(state)
        estate = await deps.repo.get_estate(state["estate_id"])  # type: ignore[arg-type]
        assert estate is not None
        specialists = {e.id: e for e in await deps.repo.specialists_of(estate.administrator_id)}
        equipment = next(
            (e for e in state.get("equipment_list", []) if e["id"] == state.get("equipment_id")), None
        )
        maintainer = specialists.get(equipment["maintainer_id"]) if equipment else None
        category_id = cfg.specialist_by_category.get(state.get("category") or "")
        by_category = specialists.get(category_id) if category_id else None
        proposed = maintainer or by_category

        work_type = state.get("work_type") or "repair"
        objection = state.get("objection")
        reason: str | None = None
        if objection:
            reason = f"caller_objection:{objection}"
        elif work_type != "repair":
            reason = f"work_type:{work_type}"
        elif proposed is None:
            reason = (
                f"nonexistent_specialist:{category_id}"
                if category_id
                else "no_specialist_for_category"
            )
        suggestion = _suggestion(work_type, objection, equipment, maintainer, proposed)
        if reason is not None:
            return _advance(
                state,
                "create_ticket",
                specialist_id=None,
                specialist_name=None,
                review_reason=reason,
                suggestion=suggestion,
                validations=[*state.get("validations", []), "pending_administrator"],
                trace=_with_trace(state, node, "for_administrator", reason),
            )
        assert proposed is not None
        return _advance(
            state,
            "create_ticket",
            specialist_id=proposed.id,
            specialist_name=proposed.name,
            suggestion=suggestion,
            validations=[
                *state.get("validations", []),
                "equipment_maintainer"
                if maintainer
                else "specialist_belongs_to_administrator",
            ],
            trace=_with_trace(
                state, node, "assigned_maintainer" if maintainer else "assigned", proposed.name
            ),
        )

    async def create_ticket(state: CallState) -> dict[str, Any]:
        node = "create_ticket"
        incident = Incident(
            id=_new_id("inc"),
            call_id=state["call_id"],
            estate_id=state["estate_id"],  # type: ignore[arg-type]
            home_id=state["home_id"],  # type: ignore[arg-type]
            description=state.get("request") or "",
            category=state["category"],  # type: ignore[arg-type]
            specialist_id=state.get("specialist_id"),
            urgency=state["urgency"],  # type: ignore[arg-type]
            state="assigned" if state.get("specialist_id") else "pending",
            created_at=datetime.now(UTC),
            equipment_id=state.get("equipment_id"),
            work_type=state.get("work_type") or "repair",  # type: ignore[arg-type]
            suggestion=_emergency_suggestion(state),
        )
        await deps.repo.save_call(_call_from(state, "incident_created"))
        await deps.repo.save_incident(incident)
        return _advance(
            state,
            "book_visit" if _can_book_visit(state) else "notify_channel",
            incident_id=incident.id,
            result="incident_created",
            trace=_with_trace(state, node, "incident_created", incident.id),
        )

    def _can_book_visit(state: CallState) -> bool:
        """Only a repair with a maintainer already assigned, and never an emergency: there the
        caller is told to call 112 and the administrator is warned first."""
        return (
            deps.tools is not None
            and bool(state.get("specialist_id"))
            and state.get("urgency") != "emergency"
        )

    async def book_visit(state: CallState) -> dict[str, Any]:
        """Tool node: ask n8n for a slot in the maintainer's calendar and hold it.

        Says nothing by itself; `notify_channel` mentions the slot only if it was booked. Any
        failure falls back to the usual closing ("he will call you to arrange the visit")."""
        node = "book_visit"
        request = {
            "call_id": state["call_id"],
            # n8n writes slot_text in the caller's language: it is spoken as is
            "language": state.get("language") or i18n.active_code(),
            "incident_id": state.get("incident_id"),
            "category": state.get("category"),
            "urgency": state.get("urgency"),
            "estate_id": state.get("estate_id"),
            "building": state.get("estate_address"),
            "apartment": state.get("home_label"),
            "specialist_id": state.get("specialist_id"),
            "specialist_name": state.get("specialist_name"),
            "summary": state.get("summary") or state.get("request"),
        }
        try:
            outcome = await deps.tools.book_visit(request)
            visit = outcome.as_state()
        except Exception as e:  # noqa: BLE001 - a tool never breaks the call
            logger.exception("book_visit falló en la llamada %s", state["call_id"])
            visit = {"status": "error", "slot": None, "booking_id": None, "reason": type(e).__name__}
        detail = visit["slot"] if visit["status"] == "booked" else str(visit.get("reason"))
        return _advance(
            state,
            "notify_channel",
            visit=visit,
            turn_timings=_timings(state, tool_ms=visit.get("ms")),
            trace=_with_trace(state, node, f"visit_{visit['status']}", detail or ""),
        )

    async def notify_channel(state: CallState) -> dict[str, Any]:
        node = "notify_channel"
        cfg = _config(state)
        if state.get("urgency") == "emergency":
            # Llamada al teléfono oficial de la finca y correo al coordinador. SIMULADOS: todavía
            # no hay contacto con el mundo real; quedan pendientes en la traza y en la incidencia
            estate = await deps.repo.get_estate(state["estate_id"])  # type: ignore[arg-type]
            phone = (
                (estate.official_phone if estate else "")
                or (cfg.contact.official_phone if cfg.contact else "")
                or "administrator"
            )
            coordinator = cfg.contact.email if cfg.contact else "coordinator"
            return _advance(
                state,
                "end",
                render("notify_channel.emergency_close"),
                finished=True,
                notice_channel="call",
                notice_target=phone,
                trace=_with_trace(
                    state,
                    node,
                    "simulated_emergency_notice",
                    f"call pending -> {phone}; email pending -> {coordinator}",
                ),
            )
        rule = cfg.rule_for(state["urgency"])  # type: ignore[arg-type]
        channel = rule.channel if rule else "ticket"
        rule_target = rule.target if rule else "specialist"
        specialist = state.get("specialist_name")
        # El vecino tiene que salir sabiendo qué va a pasar y quién le contactará. Nunca se da una
        # hora firme: nadie ha hablado aún con el profesional (regla de diseño, 2026-08-28). Con la
        # herramienta de agenda (n8n) activada se puede ofrecer un hueco, pero siempre como
        # provisional y pendiente de que lo confirme el profesional (decisión de producto,
        # 2026-11-24): `notify_channel.visit_notice`.
        contact = (
            render("notify_channel.phone_contact")
            if state.get("caller_phone")
            else render("notify_channel.generic_contact")
        )
        if specialist is None:
            target = cfg.contact.email if cfg.contact else "administrator"
            channel = "email"
            # Sin profesional para una reparación, el administrador lo asigna; si no es una
            # reparación (o el vecino puso una pega), decide cómo se resuelve
            no_professional = (state.get("review_reason") or "").startswith(
                ("no_specialist", "nonexistent_specialist")
            )
            if no_professional:
                notice = render("notify_channel.no_specialist_notice", contact=contact)
            else:
                notice = render("notify_channel.administrator_notice", contact=contact)
        elif rule_target == "coordinator":
            target = cfg.contact.email if cfg.contact else "coordinator"
            notice = render(
                "notify_channel.coordinator_notice",
                specialist=specialist,
                specialist_capitalized=_capitalize_first(specialist),
                contact=contact,
            )
        else:
            target = specialist if rule_target == "specialist" else rule_target
            visit = state.get("visit") or {}
            if visit.get("status") == "booked" and visit.get("slot"):
                notice = render(
                    "notify_channel.visit_notice",
                    specialist=specialist,
                    slot=visit["slot"],
                    contact=contact,
                )
            else:
                notice = render(
                    "notify_channel.specialist_notice", specialist=specialist, contact=contact
                )
        message = render("notify_channel.closing", notice=notice)
        return _advance(
            state,
            "end",
            message,
            finished=True,
            notice_channel=channel,
            notice_target=target,
            trace=_with_trace(state, node, "simulated_notice", f"{channel} -> {target}"),
        )

    return {
        "next_step": next_step,
        "identify_estate": identify_estate,
        "estate_not_identified": estate_not_identified,
        "identify_home": identify_home,
        "home_not_identified": home_not_identified,
        "confirm_home": confirm_home,
        "collect_request": collect_request,
        "classify_problem": classify_problem,
        "ask_detail": ask_detail,
        "determine_urgency": determine_urgency,
        "handle_emergency": handle_emergency,
        "confirm_summary": confirm_summary,
        "validate_data": validate_data,
        "unclassifiable": unclassifiable,
        "handoff_to_human": handoff_to_human,
        "farewell": farewell,
        "assign_specialist": assign_specialist,
        "create_ticket": create_ticket,
        "book_visit": book_visit,
        "notify_channel": notify_channel,
    }
