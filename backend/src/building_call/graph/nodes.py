"""Nodos del flujo de llamada.

Reglas (bóveda, 04 Arquitectura): el LLM interpreta y extrae; el código valida y decide.
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
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from time import perf_counter
from typing import Any
from uuid import uuid4

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
from building_call.llm import wire
from building_call.llm.base import LanguageModel
from building_call.llm.interpretation import (
    INTERPRETATION_VERSION,
    Interpretation,
    interpret,
)
from building_call.llm.prompts import (
    CLASSIFICATION_VERSION,
    classification_schema,
    classification_system_prompt,
)
from building_call.repos.base import Repository

logger = logging.getLogger(__name__)

ESTATE_THRESHOLD = 0.6  # Pendiente: calibrar con datos reales (bóveda, 02 Flujos)


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

    def discard_speculations(self, call_id: str) -> None:
        for future in self.speculations.pop(call_id, {}).values():
            future.cancel()


def speculation_key(text: str) -> str:
    """Clave para casar un transcript parcial con el final (sin puntuación ni mayúsculas)."""
    return _basic_normalize(text)


# Cómo se dice cada categoría en voz alta (los códigos van sin tildes)
_SPOKEN_CATEGORY = {
    "plumbing": "fontanería",
    "locksmith": "cerrajería",
    "masonry": "albañilería",
    "gardening": "jardinería",
    "administrative": "gestión administrativa",
}


def spoken_category(category: str) -> str:
    return _SPOKEN_CATEGORY.get(category, category.replace("_", " "))


def _capitalize_first(text: str) -> str:
    """Como `str.capitalize` pero sin pasar a minúsculas el resto ("Fontanería Pérez")."""
    return text[:1].upper() + text[1:]


def equipment_for(state: CallState) -> list[tuple[str, str]] | None:
    """(id, descripción) de los equipos de la finca, para que el modelo sepa a cuál afecta."""
    return [(e["id"], e["description"]) for e in state.get("equipment_list", [])] or None


def catalog_for(state: CallState) -> list[str]:
    return _catalog(state)


# --------------------------------------------------------------------------- normalización


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _basic_normalize(text: str) -> str:
    """Minúsculas, sin acentos ni signos. No elimina palabras."""
    # º/ª se descomponen en "o"/"a" con NFKD ("3º" -> "3o"); se eliminan antes.
    text = re.sub(r"[ºª°]", " ", text.lower())
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_NUMBERS = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100,
}  # fmt: skip


def _numbers_to_digits(text: str) -> str:
    """'treinta y dos' -> '32', 'doce' -> '12'. El STT puede dictar números en letra."""
    tokens = text.split()
    output: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _NUMBERS:
            value = _NUMBERS[t]
            if (
                value >= 30
                and value % 10 == 0
                and i + 2 < len(tokens)
                and tokens[i + 1] == "y"
                and tokens[i + 2] in _NUMBERS
                and _NUMBERS[tokens[i + 2]] < 10
            ):
                value += _NUMBERS[tokens[i + 2]]
                i += 2
            output.append(str(value))
        else:
            output.append(t)
        i += 1
    return " ".join(output)


def _normalize(text: str) -> str:
    """Normalización para direcciones: números en letra a cifras y sin palabras vacías."""
    text = _numbers_to_digits(_basic_normalize(text))
    text = re.sub(
        r"\b(calle|c|avenida|avda|av|plaza|pza|paseo|numero|n|del|de|la|el|los|las|en|vivo|llamo|desde|es)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def _address_similarity(utterance: str, address: str) -> float:
    a, b = _normalize(utterance), _normalize(address)
    if not a or not b:
        return 0.0
    tokens_b = set(b.split())
    contained = sum(1 for t in tokens_b if t in a.split())
    coverage = contained / len(tokens_b)
    return max(SequenceMatcher(None, a, b).ratio(), coverage)


# Lo que puede acompañar a una dirección sin aportar nada más
_ADDRESS_FILLER = frozenset(
    "vivo en la el es estoy llamo desde de del numero calle avenida plaza paseo finca mire "
    "pues hola buenas".split()
)


def _only_address(utterance: str, address: str) -> bool:
    """La frase es solo la dirección (con muletillas). "Calle Mayor 12, el portero no funciona"
    no: el resto es la avería y la tiene que leer el modelo (2026-10-21)."""
    own = set(_normalize(address).split())
    sobra = [
        t
        for t in _normalize(utterance).split()
        if t not in own and t not in _ADDRESS_FILLER and t not in _VERBAL_TICS
    ]
    return not sobra


_YES = {
    "si",
    "correcto",
    "exacto",
    "vale",
    "claro",
    "afirmativo",
    "eso es",
    "asi es",
    "ok",
    "efectivamente",
    "perfecto",
}
_NO = {"no", "incorrecto", "negativo", "para nada", "que no", "no es", "tampoco"}


def _yes_no_answer(text: str) -> bool | None:
    t = _basic_normalize(text)
    # AssemblyAI transcribe un "sí" corto como "S." y un "no" como "N." (medido 2026-08-28)
    if t in {"s", "si si", "s s"}:
        return True
    if t in {"n", "no no"}:
        return False
    words = set(t.split())
    si = {_basic_normalize(s) for s in _YES}
    no = {_basic_normalize(s) for s in _NO}
    # Frases de varias palabras ("eso es", "así es") se buscan como subcadena; el STT puede
    # anteponer ruido ("Ese, eso es.")
    has_yes = bool(words & si) or any(" " in s and s in t for s in si)
    has_no = bool(words & no) or any(" " in s and s in t for s in no)
    if has_yes and not has_no:
        return True
    if has_no or t.startswith("no"):
        return False
    return None


_FLOORS = {
    "bajo": "0", "primero": "1", "primer": "1", "primera": "1", "segundo": "2", "segunda": "2",
    "tercero": "3", "tercer": "3", "tercera": "3", "cuarto": "4", "cuarta": "4", "quinto": "5",
    "quinta": "5", "sexto": "6", "sexta": "6", "septimo": "7", "septima": "7", "octavo": "8",
    "octava": "8", "noveno": "9", "novena": "9", "decimo": "10", "decima": "10", "atico": "atico",
    "entresuelo": "entresuelo", "principal": "principal",
}  # fmt: skip


def _normalize_entrance(value: str | None) -> str | None:
    if value is None:
        return None
    v = _numbers_to_digits(_basic_normalize(str(value)))
    v = re.sub(r"\bportal\b", "", v).strip()
    # Un portal es un número (1-2 cifras) o una sola letra; "el", "ese", "sin"... son ruido
    if not v or not re.fullmatch(r"\d{1,2}|[a-z]", v):
        return None
    return v


def _normalize_floor(value: str | None) -> str | None:
    if value is None:
        return None
    v = _basic_normalize(str(value))
    v = re.sub(r"\b(piso|planta)\b", "", v).strip()
    v = _FLOORS.get(v, v)
    v = _numbers_to_digits(v)
    m = re.match(r"(\d+)", v)
    return m.group(1) if m else (v or None)


def _normalize_door(value: str | None) -> str | None:
    if value is None:
        return None
    v = _basic_normalize(str(value)).replace("puerta", "").replace("letra", "").strip()
    v = {"izquierda": "izq", "derecha": "dcha"}.get(v, v)
    return v.upper() or None


_ORDINALS = "|".join(k for k in _FLOORS if k not in {"bajo", "atico", "entresuelo", "principal"})


def _extract_home_regex(text: str) -> tuple[str | None, str | None, str | None]:
    """Respaldo determinista para formatos habituales: 'portal 2', '3º B', 'tercero B'."""
    t = _basic_normalize(text)
    m_entrance = re.search(r"\bportal\s+(\w+)", t)
    entrance = _normalize_entrance(m_entrance.group(1)) if m_entrance else None
    remainder = re.sub(r"\bportal\s+\w+", " ", t)
    m_floor = re.search(rf"\b(\d{{1,2}}|{_ORDINALS}|bajo|atico)\b\s*(?:piso|planta)?", remainder)
    floor = _normalize_floor(m_floor.group(1)) if m_floor else None
    door = None
    m_explicit = re.search(r"\b(?:puerta|letra)\s+([a-z]|\d{1,2}|izquierda|derecha)\b", remainder)
    if m_explicit:
        door = _normalize_door(m_explicit.group(1))
    else:
        m_door = re.search(r"\b([a-z]|\d{1,2}|izquierda|derecha)\b\s*$", remainder.strip())
        if m_door and (m_floor is None or m_door.start() > m_floor.start()):
            door = _normalize_door(m_door.group(1))
    return entrance, floor, door


def _home_candidates(
    homes: list[Home], entrance: str | None, floor: str | None, door: str | None
) -> list[Home]:
    """Viviendas compatibles con lo dicho. Varias => falta el portal; ninguna => no existe."""
    candidates = [
        v
        for v in homes
        if _normalize_floor(v.floor) == floor and _normalize_door(v.door) == door
    ]
    if entrance is not None:
        candidates = [v for v in candidates if _normalize_entrance(v.entrance) == entrance]
    return candidates


_META_BOT = (
    "clasific",
    "urgencia",
    "resumen",
    "entendido",
    "has dicho",
    "diras",
    "dices",
    "asistente",
    "maquina",
)


def _is_real_correction(fresh: str, previous: str, utterance: str) -> bool:
    """Una corrección del problema debe aportar algo nuevo y hablar del problema, no del bot.

    'Dirás que has podido clasificarlo con urgencia baja' es un comentario sobre el asistente:
    el modelo lo devolvió como `problem` y el grafo lo tomó como corrección (2026-08-28)."""
    e = _basic_normalize(utterance)
    if any(m in e for m in _META_BOT):
        return False
    a, b = set(_basic_normalize(fresh).split()), set(_basic_normalize(previous).split())
    if not a:
        return False
    return len(a - b) / len(a) >= 0.4  # al menos un 40% de palabras nuevas


_NOT_HEARD = (
    "estas ahi",
    "esta ahi",
    "sigues ahi",
    "sigue ahi",
    "me lo repite",
    "me lo repites",
    "repite",
    "repita",
    "no le he oido",
    "no le oigo",
    "no te oigo",
    "que has dicho",
    "que ha dicho",
    "como dice",
)


# Palabras que pueden acompañar a una frase hecha sin cambiar su sentido. Todo lo demás es
# contenido: "se repite el ruido" o "espere, que el ascensor no va" no son frases hechas.
_VERBAL_TICS = frozenset(
    "ah ay eh oh pues vale bueno mire oiga oye perdone perdona perdon disculpe hola buenas "
    "como que bien por favor si no lo le me te usted senor senora es".split()
)


def _only_set_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """La frase aparece entera y el resto son muletillas. Sustituye a la búsqueda por subcadena,
    que secuestraba incidencias reales ("tarda", "cuando", "repite") (2026-10-21)."""
    words = _basic_normalize(text).split()
    if not words or any(p.isdigit() for p in words):
        return False
    for phrase in phrases:
        f = phrase.split()
        for i in range(len(words) - len(f) + 1):
            if words[i : i + len(f)] == f:
                remainder = words[:i] + words[i + len(f) :]
                if all(p in _VERBAL_TICS for p in remainder):
                    return True
    return False


def _did_not_hear(text: str, state: CallState) -> bool:
    """Pide repetir, o saluda/pregunta si hay alguien tras un mensaje que no llegó a sonar entero."""
    t = _basic_normalize(text)
    if _only_set_phrase(text, _NOT_HEARD):
        return True
    delivery = state.get("last_delivery") or {}
    not_delivered = delivery.get("state") in ("interrupted", "not_played") or (
        delivery.get("ratio") is not None and delivery.get("ratio", 1.0) < 0.8
    )
    return bool(
        not_delivered and (t in {"hola", "oiga", "oye", "diga", "hola hola"} or t.startswith("hola"))
    )


_FILLER = (
    "espere",
    "esperame",
    "espereme",
    "un momento",
    "un segundo",
    "ahora mismo",
    "a ver",
    "dejeme",
    "voy a mirar",
    "no cuelgue",
)


def _is_filler(text: str) -> bool:
    """'Ah, vale, espéreme': el vecino pide tiempo. Ni dato ni fallo: se espera."""
    return _only_set_phrase(text, _FILLER)


_GREETINGS = (
    "hola",
    "buenas",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "que tal",
    "oiga",
    "digame",
)


def _is_greeting(text: str) -> bool:
    """Solo saludo/muletilla, sin datos: no debe contar como intento fallido."""
    return _only_set_phrase(text, _GREETINGS)


# --------------------------------------------------------------------------- a quién se avisa

_WORK_TYPE_REASON = {
    "replacement": "Hay que cambiarlo entero o hacer obra: necesita presupuesto y aprobación.",
    "insurance_claim": "Vandalismo o daños de un tercero: valorar el parte al seguro de la comunidad.",
    "neighbour_dispute": "Es un problema de convivencia entre vecinos, no una avería.",
    "private_unit": "Parece estar dentro de una vivienda particular, no en zona común.",
}


def _suggestion(
    work_type: str,
    objection: str | None,
    equipment: dict[str, Any] | None,
    maintainer: Any,
    proposed: Any,
) -> str | None:
    """Texto para quien recibe la incidencia: equipo, contrato y por qué le llega."""
    parts: list[str] = []
    if objection:
        parts.append(f"El vecino discrepa: «{objection}».")
    if work_type in _WORK_TYPE_REASON:
        parts.append(_WORK_TYPE_REASON[work_type])
    if equipment:
        data = [f"Equipo: {equipment['description']}"]
        if maintainer:
            data.append(f"mantenedor {maintainer.name} ({maintainer.target})")
        if equipment.get("contract"):
            data.append(f"contrato {equipment['contract']}")
        if equipment.get("warranty_until"):
            data.append(f"garantía hasta {equipment['warranty_until']}")
        parts.append(", ".join(data) + ".")
    if proposed is not None and (objection or work_type != "repair"):
        parts.append(f"Profesional que se habría avisado: {proposed.name}.")
    return " ".join(parts) or None


def _emergency_suggestion(state: CallState) -> str | None:
    """La incidencia de una emergencia lleva delante que los avisos están pendientes."""
    suggestion = state.get("suggestion")
    if state.get("urgency") != "emergency":
        return suggestion
    note = (
        "EMERGENCIA: llamar al teléfono oficial de la finca y avisar al coordinador. "
        "Avisos simulados: el asistente todavía no llama ni envía nada."
    )
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
        notice = str(text).split("¿", 1)[0]
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
        "last_question": text[text.index("¿") :]
        if text.startswith("Hola") and "¿" in text
        else text,
    }


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
        declared_name=state.get("declared_name"),
        transcript=state.get("history", []),
        summary=state.get("summary") or state.get("request"),
        result=result,  # type: ignore[arg-type]
        review_reason=reason,
        config_id=state.get("config_id"),
        graph=state.get("graph"),
    )


# --------------------------------------------------------------------------- interpretación


def _without_repeat_tag(text: str) -> str:
    """'Claro, le repito. X' -> 'X', para que repetir dos veces no encadene la coletilla."""
    tag_phrases = tuple(render(c, message="") for c in ("global.repeat", "global.not_heard"))
    while text.startswith(tag_phrases):
        text = next(text[len(c) :] for c in tag_phrases if text.startswith(c))
    return text


# Mientras se piden finca o vivienda, un "problema" que el modelo no sabe clasificar suele ser la
# respuesta mal transcrita ("Primero abre la"), no una avería: guardarlo saltaba "¿qué ocurre?".
_IDENTIFICATION_NODES = frozenset({"identify_estate", "identify_home"})
#: "Primero abre la": lo que el STT deja de una respuesta cortada cabe en pocas palabras
_CUT_PHRASE_WORDS = 3


def _merge_pending(state: CallState, interp: Interpretation) -> dict[str, str]:
    queued = dict(state.get("pending", {}))
    # Sin categoría del catálogo tampoco vale: el modelo puede omitirla en vez de decir
    # no_clasificable. Pero solo en frases muy cortas: "el portero automático no funciona" no
    # tiene categoría propia y es una avería de verdad (2026-10-21)
    doubtful_problem = (
        interp.category in (None, UNCLASSIFIABLE_CATEGORY)
        and state.get("current_node") in _IDENTIFICATION_NODES
        and len((interp.problem or "").split()) <= _CUT_PHRASE_WORDS
    )
    for attr in ("address", "entrance", "floor", "door", "name", "problem"):
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


_PERSON_WORDS = (
    "persona",
    "humano",
    "operador",
    "operadora",
    "alguien",
    "hablar con",
    "que me atienda",
    "maquina",
    "robot",
    "administrador",
)
_FAREWELL_WORDS = (
    "adios",
    "hasta luego",
    "hasta pronto",
    "nada mas",
    "ya llamare",
    "dejalo",
    "olvidalo",
    "no quiero seguir",
    "cuelgo",
    "gracias adios",
)


def _asks_for_person(text: str) -> bool:
    t = _basic_normalize(text)
    return any(p in t for p in _PERSON_WORDS)


def _says_goodbye(text: str) -> bool:
    t = _basic_normalize(text)
    return any(p in t for p in _FAREWELL_WORDS)


_DETAIL_QUESTIONS: dict[str, str] = {
    "electrical": "Para orientar al electricista: ¿es una sola luz o varias, y no encienden o parpadean?",
    "plumbing": "Para orientar al fontanero: ¿gotea o sale agua con fuerza, y sabe de dónde viene?",
    "elevator": "¿El ascensor está parado del todo o funciona a ratos? ¿Hay alguien dentro?",
    "locksmith": "¿La puerta no cierra, no abre, o es la cerradura? ¿Desde cuándo?",
    "cleaning": "¿En qué zona exactamente y desde cuándo?",
    "masonry": "¿Dónde exactamente está el daño y qué tamaño tiene, aproximadamente?",
    "painting": "¿Qué zona hay que pintar exactamente y qué le pasa: humedad, desconchones, pintadas?",
    "_": "¿Puede darme algún detalle más: dónde exactamente y desde cuándo ocurre?",
    # Sin saber qué falla ("está parado") no se adivina la categoría: se pregunta
    UNCLASSIFIABLE_CATEGORY: "¿Qué es lo que falla exactamente y dónde está?",
}


_FAQ: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("grab", "registr"),
        "Sí: la llamada se registra para gestionar su incidencia y para control de calidad. ",
    ),
    (
        ("tard", "when", "plazo", "vend"),
        "El plazo depende del especialista; el administrador le informará del seguimiento. ",
    ),
    (
        (
            "para que necesita",
            "para que quiere",
            "para que mi nombre",
            "por que mi nombre",
            "para que el nombre",
        ),
        "No necesito su nombre: con la dirección y la vivienda es suficiente. ",
    ),
    (
        ("quien es", "quien eres", "maquina", "robot", "eres una persona", "es usted una persona"),
        "Soy el asistente automático del administrador de su comunidad. ",
    ),
    (("coste", "cuesta", "pagar", "precio"), "Esta llamada no tiene coste adicional para usted. "),
)


# Indicación de seguridad de una emergencia según lo que cuenta el vecino. Por palabras clave:
# es un camino determinista (0 ms) y el texto de seguridad nunca lo decide el modelo.
_GAS_WORDS = frozenset({"gas", "butano", "propano"})
_FIRE_WORDS = frozenset(
    {
        "fuego",
        "humo",
        "humareda",
        "incendio",
        "incendiando",
        "incendiado",
        "llamas",
        "ardiendo",
        "quemando",
        "quemado",
    }
)


def _safety_instruction(text: str) -> str:
    words = set(_basic_normalize(text).split())
    if words & _GAS_WORDS:
        return render("handle_emergency.gas_safety")
    if words & _FIRE_WORDS:
        return render("handle_emergency.fire_safety")
    return render("handle_emergency.general_safety")


def _answer_question(text: str) -> str:
    t = _basic_normalize(text)
    for keys, reply in _FAQ:
        if any(c in t for c in keys):
            return reply
    return "Solo puedo gestionar incidencias y solicitudes de su finca. "


def build_nodes(deps: Dependencies) -> dict[str, Any]:
    """Devuelve {nombre_nodo: función async} para registrarlos en un grafo."""

    async def _interpret(
        state: CallState, utterance: str, node: str, *, yes_no: bool = False
    ) -> tuple[Interpretation, dict[str, Any]]:
        """Determinista cuando basta; si no, una llamada al LLM. Devuelve (interp, tiempos)."""
        if yes_no:
            r = _yes_no_answer(utterance)
            if r is not None and len(utterance.split()) <= 4:
                interp = Interpretation(confirms=r, origin="deterministic")
                return interp, _timings(state, interpretation_origin="deterministic")
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
        )
        return interp, _timings(
            state, interpretation_ms=interp.llm_ms, llm_calls=1, interpretation_origin="llm"
        )

    def _interp_view(interp: Interpretation) -> dict[str, Any]:
        return {
            **interp.model_dump(exclude_none=True),
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
        last = state.get("last_question") or "¿En qué puedo ayudarle?"
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
                    entrance = None  # la finca no tiene portales: el "portal" es ruido
                candidates = _home_candidates(homes, entrance, nf_, nd)
                trace.append(
                    Trace(
                        node=node,
                        action="home",
                        detail=f"entrance={entrance} piso={nf_} puerta={nd} candidatas={len(candidates)}",
                    )
                )
                if len(candidates) == 1:
                    home = candidates[0]
                    for k in ("entrance", "floor", "door"):
                        queued.pop(k, None)
                    changes.update(home_id=home.id, home_label=home.label())
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
                            entrances=" o ".join(entrances),
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
                falta = "la puerta" if floor else "el piso" if door else "el piso y la puerta"
                has = ", ".join(
                    f"{k} {v}"
                    for k, v in (("entrance", queued.get("entrance")), ("floor", floor), ("door", door))
                    if v
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

        # El nombre no se pregunta: con la finca y la vivienda basta. Si el vecino lo dice por
        # su cuenta, se guarda para el resumen y la ficha.
        if not state.get("declared_name") and (name := queued.pop("name", None)):
            changes["declared_name"] = name
            trace.append(Trace(node=node, action="name_registered", detail=name))
            state = {**state, **changes}  # type: ignore[typeddict-item]

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
                        "prompt_version": INTERPRETATION_VERSION,
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
        parts = []
        if entrances:
            parts.append("portales " + " y ".join(entrances))
        parts.append(
            f"pisos del {floors[0]} al {floors[-1]}" if len(floors) > 1 else f"piso {floors[0]}"
        )
        parts.append(
            "puertas " + " o ".join(doors) if len(doors) > 1 else f"puerta {doors[0]}"
        )
        return "En esa finca hay " + ", ".join(parts) + ". "

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
            floor=f"{floor}º" if floor.isdigit() else floor,
            doors=" o ".join(doors),
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

    async def _estate_matches(address: str) -> bool:
        estates = await deps.repo.list_estates()
        return any(_address_similarity(address, f.address) >= ESTATE_THRESHOLD for f in estates)

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
            if interp.confirms is True and not interp.address:
                estate = await deps.repo.get_estate(candidate)
                assert estate is not None
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
                    trace=_with_trace(state, node, "candidate_rejected", "nueva dirección"),
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
            trace=_with_trace(state, node, "call_logged", "sin incidencia técnica"),
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
            return _ask(
                state,
                node,
                state.get("last_question") or render("identify_home.reask"),
                history=_record_utterance(state),
                turn_timings=_timings(state, interpretation_origin="deterministic"),
                interpretation={"confirms": True, "origin": "deterministic", "llm_ms": 0},
            )
        # Formato corto y reconocible: sin LLM (bóveda: regex solo para formatos)
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
                    state, node, "regex_extraction", f"entrance={entrance} piso={floor} puerta={door}"
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
        if utterance is None:
            return _ask(
                state,
                node,
                render("home_not_identified.collect"),
            )
        history = _record_utterance(state)
        cfg = _config(state)
        target = cfg.contact.email if cfg.contact else "administrator"
        call = _call_from(
            {**state, "history": history, "request": utterance}, "home_not_identified"
        )
        await deps.repo.save_call(call)
        return _advance(
            state,
            "end",
            render("home_not_identified.farewell"),
            history=history,
            request=utterance,
            result="home_not_identified",
            finished=True,
            notice_channel="email",
            notice_target=target,
            trace=_with_trace(
                state, node, "simulated_email", f"resumen al administrador -> {target}"
            ),
        )

    async def collect_request(state: CallState) -> dict[str, Any]:
        node = "collect_request"
        utterance = state.get("user_utterance")
        if utterance is None:
            greeting = (
                render("collect_request.greeting_with_name", name=state.get("declared_name"))
                if state.get("declared_name")
                else render("collect_request.greeting")
            )
            return _ask(state, node, render("collect_request.ask", greeting=greeting))
        interp, history, extra, output = await _turn(state, node)
        if output:
            return output
        queued = _merge_pending(state, interp)
        if not queued.get("problem"):
            # Aquí lo que diga el vecino es la solicitud, aunque el modelo no lo haya marcado
            queued["problem"] = utterance.strip()
        else:
            queued["problem"] = utterance.strip()  # la descripción literal manda; el resumen va aparte
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
        if not category or (equipment_list and not state.get("equipment_evaluated")):
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
                "prompt_version": CLASSIFICATION_VERSION,
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
            if equipment:
                # La pregunta de la categoría ("para orientar al electricista") no sirve para un
                # portero o un ascensor concretos
                question = f"¿Qué le pasa exactamente al {equipment['kind']} y desde cuándo?"
            else:
                question = _DETAIL_QUESTIONS.get(
                    state.get("category") or "", _DETAIL_QUESTIONS["_"]
                )
            return _ask(state, node, question, detail_requested=True)
        interp, history, extra, output = await _turn(state, node)
        if output:
            return output
        detail = utterance.strip()
        if _yes_no_answer(utterance) is not None and len(utterance.split()) <= 3:
            detail = ""  # "no", "no sé": sin detalle útil
        request = state.get("request") or ""
        if detail:
            request = f"{request} Detalle: {detail}"
        summary = state.get("summary") or ""
        if interp.problem and interp.problem.lower() not in summary.lower():
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
            # La categoría y la urgencia son internas: no se leen al vecino (Andreu, 2026-10-21).
            # Se siguen pasando para que una plantilla publicada que las use no se rompa.
            category = state.get("category") or ""
            # El nombre se repite para que el vecino pueda corregir lo que el STT oyó mal
            who = f"{name}, " if (name := state.get("declared_name")) else ""
            return _ask(
                state,
                node,
                render(
                    "confirm_summary.summary",
                    who=who,
                    summary=(state.get("summary") or "").rstrip(". "),
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
        if interp.name and interp.confirms is not True and not interp.problem:
            changes.update(declared_name=interp.name, confirmed=None)
            name_trace = _with_trace(state, node, "correction", f"nombre -> {interp.name}")
            if interp.floor or interp.door or interp.entrance:
                changes.update(home_id=None, home_label=None)
                for k in ("entrance", "floor", "door"):
                    if getattr(interp, k):
                        queued[k] = getattr(interp, k)
            return _advance(
                state,
                "next_step",
                render("confirm_summary.correcting"),
                history=history,
                pending=queued,
                trace=name_trace,
                **changes,
                **extra,
            )
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
        elif interp.floor or interp.door or interp.entrance:
            changes.update(home_id=None, home_label=None, confirmed=None)
            for k in ("entrance", "floor", "door"):
                if getattr(interp, k):
                    queued[k] = getattr(interp, k)
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
                trace=_with_trace(state, node, "rechazado", "vuelve a recoger_solicitud"),
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
        """Emergencia con el tratamiento especial activado (bóveda, Confirmado por Andreu).

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
                    address=state.get("estate_address") or "su finca",
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
            request = f"{request} Detalle: {utterance.strip()}"
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
        # Regla de Andreu (2026-08-28): nosotros sustituimos al administrador. Con finca, vivienda
        # y solicitud confirmadas SIEMPRE se crea la incidencia; sin categoría o sin especialista
        # queda pendiente a nombre del administrador, nunca "escapa a lo que puedo gestionar".
        category = state.get("category")
        if category == UNCLASSIFIABLE_CATEGORY or category not in cfg.category_catalog:
            validations.append("sin_categoria->pending_administrator")
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
                state, node, "simulated_email", f"resumen para revisión -> {target}"
            ),
        )

    async def handoff_to_human(state: CallState) -> dict[str, Any]:
        node = "handoff_to_human"
        cfg = _config(state)
        target = cfg.contact.email if cfg.contact else "administrator"
        await deps.repo.save_call(
            _call_from(state, "handed_to_administrator", "vecino pide persona")
        )
        return _advance(
            state,
            "end",
            render("handoff_to_human.farewell"),
            result="handed_to_administrator",
            finished=True,
            notice_channel="email",
            notice_target=target,
            trace=_with_trace(state, node, "simulated_email", f"derivación -> {target}"),
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
        """A quién va la incidencia (regla de Andreu, 2026-10-21, Provisional).

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
            "notify_channel",
            incident_id=incident.id,
            result="incident_created",
            trace=_with_trace(state, node, "incident_created", incident.id),
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
                    f"llamada pendiente -> {phone}; correo pendiente -> {coordinator}",
                ),
            )
        rule = cfg.rule_for(state["urgency"])  # type: ignore[arg-type]
        channel = rule.channel if rule else "ticket"
        rule_target = rule.target if rule else "specialist"
        specialist = state.get("specialist_name")
        # El vecino tiene que salir sabiendo qué va a pasar y quién le contactará. No prometemos
        # hora: nadie ha hablado aún con el profesional (regla de Andreu, 2026-08-28).
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
        "notify_channel": notify_channel,
    }
