"""Interpretación de un turno del vecino: una sola llamada al LLM por turno.

El modelo extrae todo lo que el vecino haya dicho (aunque no sea lo que se le preguntó), una
intención global y, si el nodo lo pide, la clasificación de la incidencia. El código decide qué
hacer con ello (bóveda, 04 Arquitectura).

Latencia (medido con qwen3.5:4b en RTX 4070 SUPER, 2026-08-28): claves largas + nulos = 71 tokens
de salida ≈ 1 270 ms; claves cortas y solo claves con valor = 15 tokens ≈ 440 ms. El prompt de
sistema es constante por tarea para aprovechar la caché KV de Ollama.
"""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from building_call import i18n
from building_call.domain.config import URGENCIES
from building_call.domain.entities import WORK_TYPES
from building_call.llm import prompts_en, wire
from building_call.llm.base import LanguageModel
from building_call.llm.prompts import described_catalog

#: Spanish prompt version. The English one (also used by languages without their own prompt)
#: is `prompts_en.INTERPRETATION_VERSION`
INTERPRETATION_VERSION = "interpretacion-v8"

Intent = Literal[
    "continue",  # responde a lo preguntado o aporta datos
    "repeat",  # no ha entendido, pide que se repita
    "correct",  # corrige un dato dado antes
    "human",  # quiere hablar con una persona
    "hang_up",  # despedida o quiere terminar
    "out_of_scope",  # pide algo que el asistente no gestiona
    "question",  # hace una pregunta al asistente (¿esto se graba?, ¿cuánto tardan?)
]

INTENTS: tuple[str, ...] = (
    "continue",
    "repeat",
    "correct",
    "human",
    "hang_up",
    "out_of_scope",
    "question",
)

DATA_FIELDS: tuple[str, ...] = ("address", "entrance", "floor", "door", "name", "problem")

_EMPTY_MARKERS = {
    "no especificado", "no especificada", "desconocido", "desconocida", "ninguno", "ninguna",
    "null", "none", "n/a", "na", "sin especificar", "no indicado", "no dice", "no menciona",
    "vacio", "vacío", "-", "?", "no aplica",
    "unknown", "unspecified", "not specified", "not mentioned", "not given", "not provided",
    "nothing", "empty", "not applicable", "no", "n a",
}  # fmt: skip

# Claves del JSON del modelo -> nombres del dominio, en `wire` (medido: las claves
# abreviadas bajan la exactitud de 98% a 82%)
_KEYS: dict[str, str] = wire.KEYS


class Interpretation(BaseModel):
    intent: Intent = "continue"
    confirms: bool | None = None
    address: str | None = None
    entrance: str | None = None
    floor: str | None = None
    door: str | None = None
    name: str | None = None
    problem: str | None = None
    question: str | None = None
    # Solo cuando se pide clasificar en la misma llamada
    category: str | None = None
    urgency: str | None = None
    summary: str | None = None
    work_type: str | None = None
    equipment: str | None = None
    # Acepta lo dicho pero discrepa de cómo se va a resolver: no es una corrección de datos
    objection: str | None = None
    # Métrica: milisegundos de la llamada al modelo (0 si no se llamó)
    llm_ms: int = Field(default=0, exclude=True)
    origin: Literal["llm", "deterministic", "classifier"] = Field(default="llm", exclude=True)

    @model_validator(mode="after")
    def _split_apartment(self) -> Interpretation:
        # "apartment 3A" / "el 3B" sometimes comes back as door "3A": in this numbering it means
        # floor 3, door A, so the bot doesn't ask for a floor it was already told. It also wins
        # over a floor taken from the problem ("the light is out on the second floor, I'm in 1A")
        if self.door:
            m = re.fullmatch(r"\s*(\d{1,2})\s*([A-Za-z])\s*", self.door)
            if m:
                self.floor, self.door = m.group(1), m.group(2).upper()
        return self

    def present_fields(self) -> list[str]:
        return [c for c in DATA_FIELDS if getattr(self, c) not in (None, "")]


BASE_SYSTEM = """Eres el módulo de comprensión de un asistente telefónico de incidencias de comunidades de vecinos (España). Extrae SOLO lo que el vecino dice explícitamente en su último mensaje; no inventes.

Claves:
- intencion: continuar (responde o aporta datos; es el valor por defecto, puedes omitirla) | repetir (pide repetir, no ha entendido) | corregir (rectifica un dato anterior: "no, era el cuarto", "me he equivocado") | humano (quiere hablar con una persona) | colgar (se despide o quiere terminar) | fuera_ambito (pide algo ajeno a incidencias de la finca: cuotas, juntas, otros vecinos) | pregunta (pregunta al asistente sin aportar datos: "¿esto se graba?", "¿cuánto tardan?").
- confirma: true/false solo si responde a una pregunta de sí/no ("eso es", "correcto" = true; "no", "no es..." = false).
- direccion: calle y número de la finca, tal cual los dice.
- portal, piso, puerta: solo la vivienda del propio vecino, nunca dónde está el problema ("no hay luz en el rellano del segundo, yo vivo en el 1º A" = piso 1, puerta A). Piso: número o palabra (tercero, bajo, ático). Puerta: letra o número. Si corrige la vivienda, da siempre el piso y la puerta buenos completos, aunque solo cambie uno.
- Si empieza diciendo sí o no, confirma es ese sí o ese no aunque añada más datos ("sí, sí, ya hemos salido" = true; "no, no es ahí, es en..." = false). Si responde solo con el problema o con su vivienda, sin decir sí ni no, no pongas confirma. Repetir lo que se le ha leído ("sí, Calle Mayor 12, sí") es true.
- "Ya se sabe" (si aparece) es lo que la llamada ya ha establecido. Corregir parte de la vivienda ("era la B", "es el 3º B") se devuelve como piso Y puerta completos, tomando de ahí lo que no cambia. Repetir algo ya sabido no es una corrección: no lo devuelvas.
- nombre: nombre con el que se presenta, sin frases.
- problema: solo si cuenta qué pasa (una avería o una solicitud). Una frase cortada sin sentido, una dirección o una vivienda no son un problema. La incidencia o solicitud en máximo 12 palabras, con sus palabras y conservando SIEMPRE dónde ocurre si lo dice (planta, zona, portal): "luz del pasillo del segundo no funciona", no "luz no funciona".
- pregunta: la pregunta que hace al asistente, si la hace.
- objecion: si acepta pero pone una pega a cómo se va a resolver ("sí, pero eso no lo arregla un electricista"), la pega en pocas palabras. No es una objeción corregir un dato."""

_CLASSIFY_SYSTEM = """
- categoria: categoría de la incidencia si describe una, exactamente una de: {catalog}. La decide el elemento que falla, nunca el sitio donde está ni lo que hay al lado: "el enchufe que hay junto al ascensor no va" es electricidad; "grieta en la pared de los buzones" es albanileria. Si no nombra el elemento que falla, "no_clasificable": no lo deduzcas del verbo aunque suela ir con uno ("se ha parado", "no va", "se ha roto", "hace ruido" no dicen qué). Si lo que falla no tiene categoría propia en el catálogo (gas, incendio, plagas…), "no_clasificable": nunca la más parecida.
- urgencia: urgencia si describe una incidencia: baja (molestia sin riesgo ni daño: bombilla fundida, goteo leve) | media (afecta al uso normal: sin luz en zonas comunes, puerta que no cierra) | alta (daño en curso o servicio esencial interrumpido: inundación, ascensor averiado, sin agua, cascotes cayendo) | emergencia (riesgo inmediato para personas: alguien atrapado, olor a gas, incendio).
- actuacion: qué pide la incidencia, si describe una: reparacion (arreglar algo que se ha estropeado; es el valor por defecto, puedes omitirla) | reposicion (cambiar algo entero o una obra nueva) | siniestro (vandalismo, robo, daños por tormenta, golpe de un tercero: va al seguro) | convivencia (ruidos, molestias o conflictos con vecinos) | privativo (dentro de una vivienda particular, no de la comunidad). Un daño causado por alguien (vandalismo, robo, un coche que golpea la puerta del garaje) es siniestro aunque también haya que arreglarlo."""

_EQUIPMENT_SYSTEM = """
- equipo: si la incidencia afecta a uno de los equipos de esta finca, su id, exactamente uno de: {equipment_list}. Si nombra el tipo de equipo ("el ascensor", "el portero") y en la finca solo hay uno de ese tipo, es ese. Si no afecta a ninguno, omítela."""

FORMAT = """

Formato: un objeto JSON en UNA sola línea, sin espacios ni saltos, incluyendo SOLO las claves con valor (nunca escribas null). Ejemplos: {"piso":"3","puerta":"B"}  {"intencion":"humano"}  {"confirma":false,"direccion":"Calle Mayor 12"}  {"nombre":"Ana","problema":"no hay luz en la escalera"}

Casos difíciles:
- Ya se sabe: vivienda piso 3, puerta A. "Perdone, era la B" -> {"intencion":"corregir","piso":"3","puerta":"B"}
- "He identificado Calle Mayor 12. ¿Es correcto?" "Sí, la Calle Mayor 12, sí" -> {"confirma":true}
- "Entonces es Calle Mayor 12, 3º A. ¿Es correcto?" "El portero automático no funciona" -> {"problema":"el portero automático no funciona"}
- "Cuénteme, ¿qué ocurre?" "Perdone, no es el 3º A, es el 3º B" -> {"intencion":"corregir","piso":"3","puerta":"B"}"""


def described_equipment(equipment_list: list[tuple[str, str]]) -> str:
    """'eq-1 (portero automático Fermax, portal); eq-2 (ascensor Orona)' para los prompts."""
    return "; ".join(f"{i} ({d})" for i, d in equipment_list)


def _uses_spanish_prompts(language: str | None) -> bool:
    return i18n.get(language).prompts == "es"


def interpretation_system_prompt(
    catalog: list[str] | None = None,
    equipment_list: list[tuple[str, str]] | None = None,
    language: str | None = None,
) -> str:
    """The system prompt in the call's language set: Spanish (tuned in Spanish) or English
    (English and every language without prompts of its own, with a note on its language)."""
    code = language or i18n.active_code()
    if _uses_spanish_prompts(code):
        base, classify, equipment, fmt = BASE_SYSTEM, _CLASSIFY_SYSTEM, _EQUIPMENT_SYSTEM, FORMAT
        described = described_catalog
        note = ""
    else:
        base, classify = prompts_en.BASE_SYSTEM, prompts_en._CLASSIFY_SYSTEM
        equipment, fmt = prompts_en._EQUIPMENT_SYSTEM, prompts_en.FORMAT
        described = prompts_en.described_catalog
        note = prompts_en.language_note(i18n.get(code).name)
    system = base
    if catalog:
        system += classify.format(catalog=described(catalog))
        if equipment_list:
            system += equipment.format(equipment_list=described_equipment(equipment_list))
    return system + fmt + note


def interpretation_version(language: str | None = None) -> str:
    if _uses_spanish_prompts(language or i18n.active_code()):
        return INTERPRETATION_VERSION
    return prompts_en.INTERPRETATION_VERSION


def _schema(
    catalog: list[str] | None,
    equipment_list: list[tuple[str, str]] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    if not _uses_spanish_prompts(language or i18n.active_code()):
        return _schema_en(catalog, equipment_list)
    # Lo que ve el modelo: claves y códigos en español (`wire`)
    props: dict[str, Any] = {
        "intencion": {"type": "string", "enum": list(wire.INTENTS)},
        "confirma": {"type": ["boolean", "null"]},
        **{
            k: {"type": ["string", "null"]}
            for k in (
                "direccion",
                "portal",
                "piso",
                "puerta",
                "nombre",
                "problema",
                "pregunta",
                "objecion",
            )
        },
    }
    if catalog:
        props["categoria"] = {"type": "string", "enum": [wire.to_model(c) for c in catalog]}
        props["urgencia"] = {"type": "string", "enum": list(wire.URGENCIES)}
        props["actuacion"] = {"type": "string", "enum": list(wire.WORK_TYPES)}
        if equipment_list:
            props["equipo"] = {"type": "string", "enum": [i for i, _ in equipment_list]}
    return {"title": "interpretation", "type": "object", "properties": props, "required": []}


def _schema_en(
    catalog: list[str] | None, equipment_list: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    """English keys and the domain codes themselves."""
    props: dict[str, Any] = {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "confirms": {"type": ["boolean", "null"]},
        **{
            k: {"type": ["string", "null"]}
            for k in (*DATA_FIELDS, "question", "objection")
        },
    }
    if catalog:
        props["category"] = {"type": "string", "enum": list(catalog)}
        props["urgency"] = {"type": "string", "enum": list(URGENCIES)}
        props["work_type"] = {"type": "string", "enum": list(WORK_TYPES)}
        if equipment_list:
            props["equipment"] = {"type": "string", "enum": [i for i, _ in equipment_list]}
    return {"title": "interpretation", "type": "object", "properties": props, "required": []}


def _known_text(known: dict[str, str], spanish: bool) -> str:
    """'building 12 Main Street; apartment floor 3, door A' in the prompt's language."""
    if spanish:
        building, apartment = "finca", "vivienda"
        names = {"entrance": "portal", "floor": "piso", "door": "puerta"}
    else:
        building, apartment = "building", "apartment"
        names = {"entrance": "entrance", "floor": "floor", "door": "door"}
    parts: list[str] = []
    if known.get("address"):
        parts.append(f"{building} {known['address']}")
    home = [f"{names[k]} {known[k]}" for k in ("entrance", "floor", "door") if known.get(k)]
    if home:
        parts.append(f"{apartment} " + ", ".join(home))
    return "; ".join(parts)


def normalize_interpretation(data: dict[str, Any], llm_ms: int = 0) -> Interpretation:
    read = wire.read(data)
    clean: dict[str, Any] = {key: read.get(key) for key in _KEYS.values() if key != "reply"}
    if clean.get("intent") not in INTENTS:
        clean["intent"] = "continue"
    c = clean.get("confirms")
    if isinstance(c, str):
        c = {"true": True, "false": False}.get(c.strip().lower())
    clean["confirms"] = c if isinstance(c, bool) else None
    for key in (
        *DATA_FIELDS,
        "question",
        "summary",
        "category",
        "urgency",
        "work_type",
        "equipment",
        "objection",
    ):
        v = clean.get(key)
        clean[key] = (
            str(v).strip() if isinstance(v, str | int | float) and str(v).strip() else None
        )
        # Marcadores de "no lo sé" que el modelo devuelve como si fueran datos
        if clean[key] and clean[key].lower() in _EMPTY_MARKERS:
            clean[key] = None
    if clean["urgency"] not in URGENCIES:
        clean["urgency"] = None
    if clean["work_type"] not in WORK_TYPES:
        clean["work_type"] = None
    return Interpretation(**clean, llm_ms=llm_ms, origin="llm")


async def interpret(
    llm: LanguageModel,
    last_question: str | None,
    utterance: str,
    node: str,
    catalog: list[str] | None = None,
    heard_text: str | None = None,
    equipment_list: list[tuple[str, str]] | None = None,
    known: dict[str, str] | str | None = None,
    language: str | None = None,
) -> Interpretation:
    """Una llamada al modelo. `catalog` añade la clasificación de la incidencia a la misma llamada.
    `heard_text`: lo que llegó a sonar del último mensaje, solo si el vecino lo cortó (ledger).
    `equipment_list`: (id, descripción) de los equipos de la finca, para saber a cuál afecta.
    `known`: lo que la llamada ya ha establecido (finca, vivienda), de `known_context`.
    `language`: el de la llamada; None = el activo (lo fija `CallManager` en cada turno)."""
    code = language or i18n.active_code()
    spanish = _uses_spanish_prompts(code)
    # A dict from `known_context`, or the text already written (the gold sets)
    known_text = known if isinstance(known, str) else _known_text(known, spanish) if known else ""
    if spanish:
        user = f'Pregunta del asistente: "{last_question or "(inicio)"}"\n'
        if known_text:
            user += f"Ya se sabe: {known_text}\n"
        if heard_text is not None:
            user += f'El vecino interrumpió al asistente; solo llegó a oír: "{heard_text}"\n'
        user += f'Respuesta del vecino: """{utterance}"""'
    else:
        user = f"Assistant's question: \"{last_question or '(start)'}\"\n"
        if known_text:
            user += f"Already known: {known_text}\n"
        if heard_text is not None:
            user += f'The caller interrupted the assistant; they only heard: "{heard_text}"\n'
        user += f"Caller's answer: \"\"\"{utterance}\"\"\""
    start = perf_counter()
    data = await llm.extract(
        interpretation_system_prompt(catalog, equipment_list, code),
        user,
        _schema(catalog, equipment_list, code),
        format_instruction="",
    )
    ms = int((perf_counter() - start) * 1000)
    result = normalize_interpretation(data, ms)
    if equipment_list and result.problem and not result.equipment:
        # The model often leaves `equipment` out even when the sentence names it ("the
        # elevator got stuck"): the building's own equipment names decide then (eval 2026-11)
        found = i18n.parser_for(code).equipment_in(utterance, equipment_list)
        if found:
            result = result.model_copy(update={"equipment": found})
    return result
