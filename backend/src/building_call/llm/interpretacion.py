"""Interpretación de un turno del vecino: una sola llamada al LLM por turno.

El modelo extrae todo lo que el vecino haya dicho (aunque no sea lo que se le preguntó), una
intención global y, si el nodo lo pide, la clasificación de la incidencia. El código decide qué
hacer con ello (bóveda, 04 Arquitectura).

Latencia (medido con qwen3.5:4b en RTX 4070 SUPER, 2026-08-28): claves largas + nulos = 71 tokens
de salida ≈ 1 270 ms; claves cortas y solo claves con valor = 15 tokens ≈ 440 ms. El prompt de
sistema es constante por tarea para aprovechar la caché KV de Ollama.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from building_call.domain.configuracion import URGENCIAS
from building_call.domain.entidades import ACTUACIONES
from building_call.llm.base import ModeloLenguaje
from building_call.llm.prompts import catalogo_descrito

VERSION_INTERPRETACION = "interpretacion-v7"

Intencion = Literal[
    "continuar",  # responde a lo preguntado o aporta datos
    "repetir",  # no ha entendido, pide que se repita
    "corregir",  # corrige un dato dado antes
    "humano",  # quiere hablar con una persona
    "colgar",  # despedida o quiere terminar
    "fuera_ambito",  # pide algo que el asistente no gestiona
    "pregunta",  # hace una pregunta al asistente (¿esto se graba?, ¿cuánto tardan?)
]

INTENCIONES: tuple[str, ...] = (
    "continuar",
    "repetir",
    "corregir",
    "humano",
    "colgar",
    "fuera_ambito",
    "pregunta",
)

CAMPOS_DATOS: tuple[str, ...] = ("direccion", "portal", "piso", "puerta", "nombre", "problema")

_MARCADORES_VACIOS = {
    "no especificado", "no especificada", "desconocido", "desconocida", "ninguno", "ninguna",
    "null", "none", "n/a", "na", "sin especificar", "no indicado", "no dice", "no menciona",
    "vacio", "vacío", "-", "?", "no aplica",
}  # fmt: skip

# Claves del JSON del modelo -> nombres del dominio (medido: las claves abreviadas bajan la exactitud de 98% a 82%)
_CLAVES: dict[str, str] = {
    k: k
    for k in (
        "intencion",
        "confirma",
        "direccion",
        "portal",
        "piso",
        "puerta",
        "nombre",
        "problema",
        "pregunta",
        "categoria",
        "urgencia",
        "resumen",
        "actuacion",
        "equipo",
        "objecion",
    )
}


class Interpretacion(BaseModel):
    intencion: Intencion = "continuar"
    confirma: bool | None = None
    direccion: str | None = None
    portal: str | None = None
    piso: str | None = None
    puerta: str | None = None
    nombre: str | None = None
    problema: str | None = None
    pregunta: str | None = None
    # Solo cuando se pide clasificar en la misma llamada
    categoria: str | None = None
    urgencia: str | None = None
    resumen: str | None = None
    actuacion: str | None = None
    equipo: str | None = None
    # Acepta lo dicho pero discrepa de cómo se va a resolver: no es una corrección de datos
    objecion: str | None = None
    # Métrica: milisegundos de la llamada al modelo (0 si no se llamó)
    llm_ms: int = Field(default=0, exclude=True)
    origen: Literal["llm", "determinista"] = Field(default="llm", exclude=True)

    def campos_presentes(self) -> list[str]:
        return [c for c in CAMPOS_DATOS if getattr(self, c) not in (None, "")]


SISTEMA_BASE = """Eres el módulo de comprensión de un asistente telefónico de incidencias de comunidades de vecinos (España). Extrae SOLO lo que el vecino dice explícitamente en su último mensaje; no inventes.

Claves:
- intencion: continuar (responde o aporta datos; es el valor por defecto, puedes omitirla) | repetir (pide repetir, no ha entendido) | corregir (rectifica un dato anterior: "no, era el cuarto", "me he equivocado") | humano (quiere hablar con una persona) | colgar (se despide o quiere terminar) | fuera_ambito (pide algo ajeno a incidencias de la finca: cuotas, juntas, otros vecinos) | pregunta (pregunta al asistente sin aportar datos: "¿esto se graba?", "¿cuánto tardan?").
- confirma: true/false solo si responde a una pregunta de sí/no ("eso es", "correcto" = true; "no", "no es..." = false).
- direccion: calle y número de la finca, tal cual los dice.
- portal, piso, puerta: si menciona la vivienda. Piso: número o palabra (tercero, bajo, ático). Puerta: letra o número. Si corrige la vivienda, da siempre el piso y la puerta buenos completos, aunque solo cambie uno.
- nombre: nombre con el que se presenta, sin frases.
- problema: solo si cuenta qué pasa (una avería o una solicitud). Una frase cortada sin sentido, una dirección o una vivienda no son un problema. La incidencia o solicitud en máximo 12 palabras, con sus palabras y conservando SIEMPRE dónde ocurre si lo dice (planta, zona, portal): "luz del pasillo del segundo no funciona", no "luz no funciona".
- pregunta: la pregunta que hace al asistente, si la hace.
- objecion: si acepta pero pone una pega a cómo se va a resolver ("sí, pero eso no lo arregla un electricista"), la pega en pocas palabras. No es una objeción corregir un dato."""

_SISTEMA_CLASIFICAR = """
- categoria: categoría de la incidencia si describe una, exactamente una de: {catalogo}. La decide el elemento que falla, nunca el sitio donde está ni lo que hay al lado: "el enchufe que hay junto al ascensor no va" es electricidad; "grieta en la pared de los buzones" es albanileria. Si no nombra el elemento que falla, "no_clasificable": no lo deduzcas del verbo aunque suela ir con uno ("se ha parado", "no va", "se ha roto", "hace ruido" no dicen qué). Si lo que falla no tiene categoría propia en el catálogo (gas, incendio, plagas…), "no_clasificable": nunca la más parecida.
- urgencia: urgencia si describe una incidencia: baja (molestia sin riesgo ni daño: bombilla fundida, goteo leve) | media (afecta al uso normal: sin luz en zonas comunes, puerta que no cierra) | alta (daño en curso o servicio esencial interrumpido: inundación, ascensor averiado, sin agua, cascotes cayendo) | emergencia (riesgo inmediato para personas: alguien atrapado, olor a gas, incendio).
- actuacion: qué pide la incidencia, si describe una: reparacion (arreglar algo que se ha estropeado; es el valor por defecto, puedes omitirla) | reposicion (cambiar algo entero o una obra nueva) | siniestro (vandalismo, robo, daños por tormenta, golpe de un tercero: va al seguro) | convivencia (ruidos, molestias o conflictos con vecinos) | privativo (dentro de una vivienda particular, no de la comunidad)."""

_SISTEMA_EQUIPOS = """
- equipo: si la incidencia afecta a uno de los equipos de esta finca, su id, exactamente uno de: {equipos}. Si nombra el tipo de equipo ("el ascensor", "el portero") y en la finca solo hay uno de ese tipo, es ese. Si no afecta a ninguno, omítela."""

FORMATO = """

Formato: un objeto JSON en UNA sola línea, sin espacios ni saltos, incluyendo SOLO las claves con valor (nunca escribas null). Ejemplos: {"piso":"3","puerta":"B"}  {"intencion":"humano"}  {"confirma":false,"direccion":"Calle Mayor 12"}  {"nombre":"Ana","problema":"no hay luz en la escalera"}"""


def equipos_descritos(equipos: list[tuple[str, str]]) -> str:
    """'eq-1 (portero automático Fermax, portal); eq-2 (ascensor Orona)' para los prompts."""
    return "; ".join(f"{i} ({d})" for i, d in equipos)


def sistema_interpretacion(
    catalogo: list[str] | None = None, equipos: list[tuple[str, str]] | None = None
) -> str:
    if catalogo:
        sistema = SISTEMA_BASE + _SISTEMA_CLASIFICAR.format(catalogo=catalogo_descrito(catalogo))
        if equipos:
            sistema += _SISTEMA_EQUIPOS.format(equipos=equipos_descritos(equipos))
        return sistema + FORMATO
    return SISTEMA_BASE + FORMATO


def _esquema(
    catalogo: list[str] | None, equipos: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "intencion": {"type": "string", "enum": list(INTENCIONES)},
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
    if catalogo:
        props["categoria"] = {"type": "string", "enum": catalogo}
        props["urgencia"] = {"type": "string", "enum": list(URGENCIAS)}
        props["actuacion"] = {"type": "string", "enum": list(ACTUACIONES)}
        if equipos:
            props["equipo"] = {"type": "string", "enum": [i for i, _ in equipos]}
    return {"title": "interpretacion", "type": "object", "properties": props, "required": []}


def normalizar_interpretacion(datos: dict[str, Any], llm_ms: int = 0) -> Interpretacion:
    limpio: dict[str, Any] = {}
    for corta, larga in _CLAVES.items():
        limpio[larga] = datos.get(corta)
    if limpio.get("intencion") not in INTENCIONES:
        limpio["intencion"] = "continuar"
    c = limpio.get("confirma")
    if isinstance(c, str):
        c = {"true": True, "false": False}.get(c.strip().lower())
    limpio["confirma"] = c if isinstance(c, bool) else None
    for clave in (
        *CAMPOS_DATOS,
        "pregunta",
        "resumen",
        "categoria",
        "urgencia",
        "actuacion",
        "equipo",
        "objecion",
    ):
        v = limpio.get(clave)
        limpio[clave] = (
            str(v).strip() if isinstance(v, str | int | float) and str(v).strip() else None
        )
        # Marcadores de "no lo sé" que el modelo devuelve como si fueran datos
        if limpio[clave] and limpio[clave].lower() in _MARCADORES_VACIOS:
            limpio[clave] = None
    if limpio["urgencia"] not in URGENCIAS:
        limpio["urgencia"] = None
    if limpio["actuacion"] not in ACTUACIONES:
        limpio["actuacion"] = None
    return Interpretacion(**limpio, llm_ms=llm_ms, origen="llm")


async def interpretar(
    llm: ModeloLenguaje,
    ultima_pregunta: str | None,
    entrada: str,
    nodo: str,
    catalogo: list[str] | None = None,
    oido: str | None = None,
    equipos: list[tuple[str, str]] | None = None,
) -> Interpretacion:
    """Una llamada al modelo. `catalogo` añade la clasificación de la incidencia a la misma llamada.
    `oido`: lo que llegó a sonar del último mensaje, solo si el vecino lo cortó (ledger).
    `equipos`: (id, descripción) de los equipos de la finca, para saber a cuál afecta."""
    usuario = f'Pregunta del asistente: "{ultima_pregunta or "(inicio)"}"\n'
    if oido is not None:
        usuario += f'El vecino interrumpió al asistente; solo llegó a oír: "{oido}"\n'
    usuario += f'Respuesta del vecino: """{entrada}"""'
    inicio = perf_counter()
    datos = await llm.extraer(
        sistema_interpretacion(catalogo, equipos),
        usuario,
        _esquema(catalogo, equipos),
        instruccion_formato="",
    )
    ms = int((perf_counter() - inicio) * 1000)
    return normalizar_interpretacion(datos, ms)
