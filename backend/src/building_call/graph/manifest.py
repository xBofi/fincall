"""Manifiesto del grafo: la estructura que el código compilado no puede contar.

`registry.py` conecta todos los nodos con todos y deja que `_next` lea `current_node`, así
que el grafo compilado de LangGraph no dice nada del flujo real. Este módulo declara, por nodo,
qué hace y a dónde puede saltar, y `nodes.py` lo verifica en cada transición: una arista que no
esté aquí declarada rompe la suite (`BC_STRICT_GRAPH=1`) y queda registrada en producción.

No es documentación decorativa. Si alguien añade una transición y no la declara, los tests fallan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

FINISH = "end"

NodeType = Literal["decision", "question", "effect", "terminal"]


@dataclass(frozen=True)
class Edge:
    target: str
    condition: str


@dataclass(frozen=True)
class ManifestNode:
    id: str
    title: str
    summary: str
    kind: NodeType
    #: Si el nodo puede provocar una llamada al modelo en su turno
    uses_llm: bool = False
    #: Atajos que se resuelven en código, sin modelo (turnos de ~0 ms)
    deterministic_paths: tuple[str, ...] = ()
    #: Efectos con consecuencias fuera del estado (BD, avisos)
    effects: tuple[str, ...] = ()
    targets: tuple[Edge, ...] = ()
    #: Ids del catálogo de plantillas que este nodo puede emitir
    templates: tuple[str, ...] = field(default_factory=tuple)
    #: Etapa de la conversación, para dibujarlo. No influye en el flujo: es la pista de
    #: disposición, y vive aquí para que el dibujo no duplique el conocimiento del flujo.
    stage: int = 0


# Los nodos que consumen un turno del vecino pasan por `_turn`, que atiende las intenciones
# globales: repetir y preguntar/fuera de ámbito vuelven al propio nodo, y `human` y `hang_up`
# (solo con palabras explícitas del vecino) derivan o se despiden.
_GLOBAL_INTENTS = (
    Edge("handoff_to_human", "el vecino pide expresamente hablar con una persona"),
    Edge("farewell", "el vecino se despide expresamente sin aportar datos"),
)

_TURN_DETERMINISTIC = (
    "no se ha oído (se repite el mensaje completo)",
    'relleno ("un momento", "a ver...")',
)


MANIFEST: dict[str, ManifestNode] = {
    "next_step": ManifestNode(
        id="next_step",
        stage=0,
        title="Siguiente paso",
        summary=(
            "El único nodo que decide el orden de las preguntas. Consume lo que el vecino ya haya "
            "dicho (aunque no fuera lo que se le preguntaba) y salta a lo primero que falte: "
            "finca, vivienda, solicitud, categoría, urgencia, detalle o confirmación. "
            "Los nodos no se encadenan entre sí: todos vuelven aquí."
        ),
        kind="decision",
        uses_llm=False,
        deterministic_paths=(
            "cotejo de dirección por similitud (umbral 0,6)",
            "búsqueda de vivienda por portal/piso/puerta",
        ),
        targets=(
            Edge("identify_estate", "no hay finca, o hay una candidata que confirmar"),
            Edge("estate_not_identified", "agotados los intentos de identificar la finca"),
            Edge("identify_home", "hay finca pero no vivienda, o falta un dato de ella"),
            Edge("home_not_identified", "agotados los intentos de identificar la vivienda"),
            Edge("confirm_home", "hay finca y vivienda pero aún no se han leído juntas"),
            Edge("collect_request", "falta saber qué le ocurre"),
            Edge("classify_problem", "hay solicitud pero no categoría"),
            Edge("determine_urgency", "hay categoría pero no urgencia"),
            Edge(
                "handle_emergency",
                "urgencia efectiva `emergency` (tratamiento activado) sin confirmar todavía",
            ),
            Edge("ask_detail", "la configuración pide un detalle y la incidencia lo merece"),
            Edge("confirm_summary", "están los datos y falta que el vecino confirme"),
            Edge("validate_data", "el vecino ya ha confirmado el resumen"),
        ),
    ),
    "identify_estate": ManifestNode(
        id="identify_estate",
        stage=1,
        title="Identificar finca",
        summary=(
            "Pregunta la calle y el número, o pide confirmación de la finca candidata que "
            "`next_step` encontró por similitud. Consume intentos: al agotarlos, deriva."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=("sí / no", "saludo inicial", *_TURN_DETERMINISTIC),
        targets=(
            Edge("identify_estate", "repregunta: no confirmada, no entendida o reintento"),
            Edge("next_step", "finca confirmada"),
            Edge("estate_not_identified", "agotados los intentos"),
            *_GLOBAL_INTENTS,
        ),
    ),
    "estate_not_identified": ManifestNode(
        id="estate_not_identified",
        stage=1,
        title="Finca no identificada",
        summary=(
            "Salida digna cuando la finca no consta: recoge lo que el vecino quería comunicar y "
            "deja la llamada registrada para que calidad la revise. No crea incidencia."
        ),
        kind="effect",
        uses_llm=False,
        effects=("guarda la llamada con resultado `estate_not_identified`",),
        targets=(
            Edge("estate_not_identified", "primera pasada: pide que cuente qué quería"),
            Edge(FINISH, "recogido el mensaje, se cierra la llamada"),
        ),
    ),
    "identify_home": ManifestNode(
        id="identify_home",
        stage=2,
        title="Identificar vivienda",
        summary=(
            "Pregunta portal, piso y puerta y los coteja contra las viviendas de la finca. "
            "Si el piso y la puerta existen en varios portales, pregunta solo el portal."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=(
            '"3º B" y variantes por expresión regular',
            *_TURN_DETERMINISTIC,
        ),
        targets=(
            Edge("identify_home", "repregunta: falta un dato o no coincide"),
            Edge("next_step", "vivienda identificada sin ambigüedad"),
            Edge("home_not_identified", "agotados los intentos"),
            *_GLOBAL_INTENTS,
        ),
    ),
    "home_not_identified": ManifestNode(
        id="home_not_identified",
        stage=2,
        title="Vivienda no identificada",
        summary=(
            "Recoge la solicitud aunque no se sepa la vivienda y avisa al administrador por "
            "correo con el teléfono del llamante. No crea incidencia ni asigna profesional."
        ),
        kind="effect",
        uses_llm=False,
        effects=(
            "guarda la llamada con resultado `home_not_identified`",
            "avisa al administrador por correo",
        ),
        targets=(
            Edge("home_not_identified", "primera pasada: pide la solicitud igualmente"),
            Edge(FINISH, "recogida la solicitud, se cierra la llamada"),
        ),
    ),
    "confirm_home": ManifestNode(
        id="confirm_home",
        stage=2,
        title="Confirmar vivienda",
        summary=(
            "Lee juntas la finca y la vivienda (\"Entonces es Calle Mayor 12, tercero B. ¿Es "
            "correcto?\") antes de preguntar qué ocurre. Una corrección se vuelve a buscar y a "
            "leer; pasar directamente al problema cuenta como un sí. En una emergencia se salta."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=("sí / no", *_TURN_DETERMINISTIC),
        targets=(
            Edge("confirm_home", "repregunta: la respuesta no fue un sí, un no ni una corrección"),
            Edge("next_step", "confirmada, o corregida (se busca y se vuelve a leer)"),
            Edge("identify_home", "el vecino dice que no es la suya sin dar la buena"),
            *_GLOBAL_INTENTS,
        ),
    ),
    "collect_request": ManifestNode(
        id="collect_request",
        stage=3,
        title="Recoger solicitud",
        summary=(
            "Pregunta qué ocurre. La interpretación del turno clasifica en la misma llamada al "
            "modelo (categoría y urgencia), para no gastar un segundo turno en clasificar."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=("FAQ (¿esto se graba?, ¿cuánto tardan?)", *_TURN_DETERMINISTIC),
        targets=(
            Edge("collect_request", "no ha contado todavía qué le ocurre"),
            Edge("next_step", "solicitud recogida"),
            *_GLOBAL_INTENTS,
        ),
    ),
    "classify_problem": ManifestNode(
        id="classify_problem",
        stage=3,
        title="Clasificar problema",
        summary=(
            "Respaldo: solo se ejecuta si la interpretación del turno no trajo ya la categoría. "
            "Valida contra el catálogo de la finca; lo que no encaje cae en `unclassifiable`."
        ),
        kind="decision",
        uses_llm=True,
        targets=(Edge("determine_urgency", "clasificado, con categoría y urgencia"),),
    ),
    "ask_detail": ManifestNode(
        id="ask_detail",
        stage=4,
        title="Pedir detalle",
        summary=(
            "Una sola pregunta de detalle antes del resumen, para que el profesional sepa a qué "
            "va. Se puede desactivar por finca (`ask_detail`) y solo se hace si el caso lo merece."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=_TURN_DETERMINISTIC,
        targets=(
            Edge("ask_detail", "no ha respondido al detalle"),
            Edge("next_step", "detalle recogido, o el vecino no lo aporta"),
            *_GLOBAL_INTENTS,
        ),
    ),
    "determine_urgency": ManifestNode(
        id="determine_urgency",
        stage=3,
        title="Determinar urgencia",
        summary=(
            "Convierte la urgencia declarada en la efectiva según la configuración. Mientras las "
            "emergencias estén desactivadas (`emergencies_enabled`), `emergency` se trata como "
            "`high`; activadas, `next_step` la lleva a `handle_emergency`."
        ),
        kind="decision",
        uses_llm=False,
        targets=(Edge("next_step", "urgencia efectiva fijada"),),
    ),
    "handle_emergency": ManifestNode(
        id="handle_emergency",
        stage=5,
        title="Atender emergencia",
        summary=(
            "Solo con `emergencies_enabled`. Da primero una indicación de seguridad (112; con gas, "
            "no encender luces ni aparatos y salir) y confirma lo mínimo: la finca y que hay una "
            "emergencia. No pide detalle ni lee el resumen largo. Si el vecino dice que no es una "
            "emergencia, sigue como urgencia alta; ante la duda, se registra igualmente."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=(
            "indicación de seguridad por palabras clave (gas, fuego/humo, resto)",
            "sí / no",
            *_TURN_DETERMINISTIC,
        ),
        targets=(
            Edge("handle_emergency", "el vecino pide repetir o hace una pregunta"),
            Edge(
                "next_step",
                "confirmada (o respuesta dudosa: se registra igual), o no es una emergencia",
            ),
            *_GLOBAL_INTENTS,
        ),
    ),
    "confirm_summary": ManifestNode(
        id="confirm_summary",
        stage=5,
        title="Confirmar resumen",
        summary=(
            "Lee al vecino quién es, qué ocurre y con qué urgencia, y espera un sí o un no. "
            "Un no con corrección vuelve a recoger la solicitud."
        ),
        kind="question",
        uses_llm=True,
        deterministic_paths=("sí / no", *_TURN_DETERMINISTIC),
        targets=(
            Edge("confirm_summary", "no se ha entendido la respuesta"),
            Edge("next_step", "confirmado, o hay una corrección que reprocesar"),
            Edge("validate_data", "confirmado y sin nada que corregir"),
            Edge("collect_request", "el vecino dice que el resumen no es correcto"),
            *_GLOBAL_INTENTS,
        ),
    ),
    "validate_data": ManifestNode(
        id="validate_data",
        stage=6,
        title="Validar datos",
        summary=(
            "Última comprobación antes de tocar la base de datos: que haya finca, vivienda, "
            "categoría dentro del catálogo y urgencia válida."
        ),
        kind="decision",
        uses_llm=False,
        targets=(
            Edge("unclassifiable", "la categoría no encaja en el catálogo de la finca"),
            Edge("assign_specialist", "datos completos y coherentes"),
        ),
    ),
    "unclassifiable": ManifestNode(
        id="unclassifiable",
        stage=8,
        title="No clasificable",
        summary=(
            "La solicitud no encaja en ninguna categoría del catálogo: queda para el "
            "administrador en vez de asignarse a un profesional equivocado."
        ),
        kind="effect",
        uses_llm=False,
        effects=("guarda la llamada con resultado `unclassifiable`",),
        targets=(Edge(FINISH, "se despide tras dejar constancia"),),
    ),
    "handoff_to_human": ManifestNode(
        id="handoff_to_human",
        stage=8,
        title="Derivar a humano",
        summary=(
            "El vecino ha pedido expresamente hablar con una persona. Exige palabras explícitas, "
            "no solo la opinión del modelo: derivar es irreversible."
        ),
        kind="terminal",
        uses_llm=False,
        effects=("guarda la llamada con resultado `derivada`",),
        targets=(Edge(FINISH, "derivada"),),
    ),
    "farewell": ManifestNode(
        id="farewell",
        stage=8,
        title="Despedida",
        summary="El vecino se despide antes de terminar el guion. Se cierra sin incidencia.",
        kind="terminal",
        uses_llm=False,
        effects=("guarda la llamada",),
        targets=(Edge(FINISH, "colgada por el vecino"),),
    ),
    "assign_specialist": ManifestNode(
        id="assign_specialist",
        stage=7,
        title="Asignar especialista",
        summary=(
            "Busca el profesional de la categoría en la configuración efectiva de la finca y "
            "comprueba que pertenece a su administrador. Sin profesional, queda pendiente."
        ),
        kind="effect",
        uses_llm=False,
        effects=("selecciona profesional, o marca la incidencia como pendiente de administrador",),
        targets=(Edge("create_ticket", "con profesional asignado o sin él"),),
    ),
    "create_ticket": ManifestNode(
        id="create_ticket",
        stage=7,
        title="Crear incidencia",
        summary="Persiste la llamada y la incidencia. Es el punto sin retorno del flujo.",
        kind="effect",
        uses_llm=False,
        effects=("guarda la llamada", "crea la incidencia"),
        targets=(Edge("notify_channel", "incidencia creada"),),
    ),
    "notify_channel": ManifestNode(
        id="notify_channel",
        stage=7,
        title="Avisar y despedir",
        summary=(
            "Aplica la regla de aviso de la urgencia (canal y destino) y le cuenta al vecino qué "
            "va a pasar y quién le llamará. Nunca promete una hora: nadie ha hablado aún con el "
            "profesional."
        ),
        kind="effect",
        uses_llm=False,
        effects=(
            "avisa por el canal de la regla (ticket, correo o llamada)",
            "emergencia: deja simulada la llamada al teléfono oficial y el correo al coordinador",
        ),
        targets=(Edge(FINISH, "incidencia creada y vecino informado"),),
    ),
}


#: Destinos declarados por nodo, para la verificación en ejecución.
TRANSITIONS: dict[str, frozenset[str]] = {
    id_: frozenset(a.target for a in n.targets) for id_, n in MANIFEST.items()
}


class UndeclaredTransition(RuntimeError):
    """Un nodo ha saltado a un destino que el manifiesto no declara."""


def strict() -> bool:
    """En tests y con `BC_STRICT_GRAPH=1` una transición no declarada es un error."""
    return os.getenv("BC_STRICT_GRAPH") == "1" or os.getenv("PYTEST_CURRENT_TEST") is not None


def verify_transition(origin: str | None, target: str) -> str | None:
    """Devuelve el motivo de la violación, o None si la transición está declarada.

    En producción nunca lanza: una arista mal declarada no puede colgarle el teléfono a un vecino.
    """
    if origin is None or origin not in TRANSITIONS:
        return None
    if target in TRANSITIONS[origin]:
        return None
    reason = f"{origin} -> {target} no está declarada en el manifiesto"
    if strict():
        raise UndeclaredTransition(reason)
    return reason
