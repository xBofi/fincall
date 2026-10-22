"""Catálogo de lo que el asistente dice, editable desde la consola.

Un nodo no tiene "un prompt": tiene **plantillas**, texto con datos interpolados. Esa plantilla
es el MENSAJE OBLIGATORIO que `llm/phrasing.py` parafrasea conservando los hechos. Los tres
prompts de sistema (interpretación, clasificación, redacción) NO se editan: declaran claves JSON
y enums que el código valida, y tocarlos degrada exactitud y latencia en silencio.

Latencia: `render` resuelve contra un diccionario en memoria. Una llamada **nunca** consulta la
base de datos para hablar; las sobrescrituras se cargan al arrancar y al guardar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Longitud máxima de un mensaje: esto se dice por teléfono, y la redacción compara longitudes
#: (`len(texto) > 2.5 * len(plantilla) + 80` descarta la paráfrasis).
MAX_CHARACTERS = 320

_VARIABLE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Template:
    id: str
    node: str
    description: str
    text: str
    variables: tuple[str, ...] = ()
    #: Si el mensaje debe terminar preguntando. `conserva_hechos()` descarta la paráfrasis que
    #: pierde el interrogante, así que una plantilla sin `?` deja la redacción inservible.
    question: bool = False
    #: Nombre de la variable que aporta el interrogante, cuando la pregunta no está en el texto
    #: sino en un dato que el nodo inyecta (por ejemplo, la pregunta anterior que se recupera).
    question_in: str | None = None
    #: Lo que dice no se puede perder: el aviso de IA y grabación y el siguiente paso del cierre.
    #: Si el vecino lo corta, el ledger de entrega hace que se diga otra vez (2026-10-21).
    notice: bool = False


class RenderedText(str):
    """El texto ya renderizado, que recuerda de qué plantilla y versión salió.

    Es un `str` normal a todos los efectos: se puede pasar donde se pasaba el literal. Lleva
    la identidad encima para que la auditoría no dependa de cablear el id por cada firma.
    """

    __slots__ = ("template_id", "version")
    template_id: str
    version: int

    def __new__(cls, text: str, template_id: str, version: int) -> RenderedText:
        obj = super().__new__(cls, text)
        obj.template_id = template_id
        obj.version = version
        return obj


def _p(
    id_: str,
    node: str,
    description: str,
    text: str,
    *,
    question: bool = False,
    question_in: str | None = None,
    notice: bool = False,
) -> tuple[str, Template]:
    variables = tuple(dict.fromkeys(_VARIABLE.findall(text)))
    assert question_in is None or question_in in variables, id_
    return id_, Template(
        id=id_,
        node=node,
        description=description,
        text=text,
        variables=variables,
        question=question or question_in is not None,
        question_in=question_in,
        notice=notice,
    )


CATALOG: dict[str, Template] = dict(
    (
        # ---------------------------------------------------------------- intenciones globales
        _p(
            "global.repeat",
            "global",
            "El vecino pide que se le repita, desde cualquier punto de la llamada.",
            "Claro, le repito. {message}",
        ),
        _p(
            "global.not_heard",
            "global",
            "Se detecta que el vecino no ha oído el mensaje (camino determinista, sin modelo).",
            "Perdone, le repito. {message}",
        ),
        _p(
            "global.filler",
            "global",
            'El vecino pide tiempo ("un momento", "a ver..."). No cuenta como intento fallido.',
            "Claro, sin prisa. Dígame cuando quiera.",
        ),
        _p(
            "global.out_of_scope",
            "global",
            "El vecino pide algo ajeno a incidencias de la finca (cuotas, juntas, otros vecinos).",
            "Lo siento, solo puedo gestionar incidencias y solicitudes de la finca. {question}",
            question_in="question",
        ),
        # ---------------------------------------------------------------- identificar finca
        _p(
            "identify_estate.greeting",
            "identify_estate",
            "Primer mensaje de la llamada. Avisa de que atiende una IA y de la grabación "
            "(RGPD y AI Act: el vecino debe saberlo desde el inicio; texto pendiente de legal).",
            "Hola, le atiende un asistente de inteligencia artificial de su comunidad. "
            "Esta llamada puede ser grabada para mejorar la calidad del servicio. "
            "¿En qué finca se encuentra? Dígame la calle y el número.",
            question=True,
            notice=True,
        ),
        _p(
            "identify_estate.greeting_reply",
            "identify_estate",
            "El vecino solo ha saludado, sin dar la dirección.",
            "Hola, buenas. ¿En qué finca se encuentra? Dígame la calle y el número.",
            question=True,
        ),
        _p(
            "identify_estate.candidate",
            "identify_estate",
            "Se ha encontrado una finca parecida a lo que dijo y hay que confirmarla.",
            "He identificado {address}. ¿Es correcto?",
            question=True,
        ),
        _p(
            "identify_estate.confirm_yes_no",
            "identify_estate",
            "No se ha entendido si confirmaba o no la finca candidata.",
            "Perdone, ¿es correcta esa finca? Responda sí o no.",
            question=True,
        ),
        _p(
            "identify_estate.retry",
            "identify_estate",
            "Reintento tras no encontrar o no entender la dirección. {reason} explica por qué.",
            "{reason} ¿Puede repetirme la calle y el número?",
            question=True,
        ),
        _p(
            "identify_estate.reason_not_found",
            "identify_estate",
            "Motivo del reintento: la dirección no está entre las fincas gestionadas.",
            "No he encontrado esa dirección entre las fincas gestionadas.",
        ),
        _p(
            "identify_estate.reason_not_understood",
            "identify_estate",
            "Motivo del reintento: no se ha entendido la dirección.",
            "No he entendido la dirección de la finca.",
        ),
        _p(
            "identify_estate.reason_not_that_one",
            "identify_estate",
            "Motivo del reintento: el vecino ha dicho que la finca candidata no es la suya.",
            "Entendido, no es esa.",
        ),
        # ---------------------------------------------------------------- finca no identificada
        _p(
            "estate_not_identified.collect",
            "estate_not_identified",
            "No consta la finca: se recoge igualmente el mensaje del vecino.",
            "Lo siento, no consigo identificar la finca; puede que no esté gestionada por este "
            "administrador. Si quiere, cuénteme brevemente qué quería comunicar y quedará "
            "registrado.",
        ),
        _p(
            "estate_not_identified.farewell",
            "estate_not_identified",
            "Cierre tras registrar la llamada para que la revise calidad.",
            "Gracias. He registrado su llamada para que el equipo de calidad la revise. "
            "Hasta luego.",
            notice=True,
        ),
        # ---------------------------------------------------------------- identificar vivienda
        _p(
            "identify_home.ask",
            "identify_home",
            "Primera pregunta por la vivienda, ya con la finca confirmada.",
            "Gracias. ¿Cuál es su vivienda? Indíqueme portal si lo hay, piso y puerta.",
            question=True,
        ),
        _p(
            "identify_home.partial",
            "identify_home",
            "El vecino ha dado parte del dato; se conserva y se pide solo lo que falta.",
            "Tomo nota: {has}. ¿Y {falta}?",
            question=True,
        ),
        _p(
            "identify_home.several_entrances",
            "identify_home",
            "Ese piso y puerta existen en varios portales: solo falta el portal.",
            "El {floor}º {door} existe en varios portales. ¿En qué portal vive, {entrances}?",
            question=True,
        ),
        _p(
            "identify_home.retry",
            "identify_home",
            "La vivienda no existe en la finca. {hint} describe las viviendas que sí existen.",
            "No encuentro esa vivienda. {hint}¿Cuál es su piso y su puerta?",
            question=True,
        ),
        _p(
            "identify_home.floor_door",
            "identify_home",
            "El piso existe pero la puerta no: se conserva el piso y se ofrecen sus puertas.",
            "En el {floor} las puertas son {doors}. ¿Cuál es la suya?",
            question=True,
        ),
        _p(
            "identify_home.reask",
            "identify_home",
            "Respaldo cuando no hay una pregunta anterior que repetir.",
            "¿Cuál es su piso y su puerta?",
            question=True,
        ),
        # ------------------------------------------------------------- vivienda no identificada
        _p(
            "home_not_identified.collect",
            "home_not_identified",
            "No se ha podido identificar la vivienda: se recoge la solicitud igualmente.",
            "No he podido identificar correctamente la vivienda. Cuénteme igualmente su "
            "solicitud; enviaré un resumen al administrador con su número de teléfono.",
        ),
        _p(
            "home_not_identified.farewell",
            "home_not_identified",
            "Cierre tras avisar al administrador por correo.",
            "Gracias. He enviado el resumen al administrador, que se pondrá en contacto con "
            "usted. Hasta luego.",
            notice=True,
        ),
        # ---------------------------------------------------------------- recoger solicitud
        _p(
            "collect_request.ask",
            "collect_request",
            "Se pregunta qué ocurre. {greeting} es el prefijo de cortesía.",
            "{greeting}Cuénteme, ¿qué ocurre?",
            question=True,
        ),
        _p(
            "collect_request.greeting",
            "collect_request",
            "Prefijo antes de preguntar qué ocurre. La vivienda ya está registrada: no se repite.",
            "Perfecto. ",
        ),
        # ---------------------------------------------------------------- confirmar resumen
        _p(
            "confirm_summary.summary",
            "confirm_summary",
            "Se le lee al vecino lo entendido y se espera un sí o un no. {who} queda vacío: el "
            "nombre del vecino no se guarda; la vivienda ya está registrada y no se lee. La "
            "categoría y la urgencia son internas y no se leen (Andreu, 2026-10-21).",
            "Le resumo: {summary}. ¿Es correcto?",
            question=True,
        ),
        _p(
            "confirm_summary.confirm_yes_no",
            "confirm_summary",
            "No se ha entendido si confirmaba el resumen.",
            "Perdone, no le he entendido. ¿Es correcto el resumen? Responda sí o no.",
            question=True,
        ),
        _p(
            "confirm_summary.correcting",
            "confirm_summary",
            "El vecino ha corregido un dato y se toma nota.",
            "De acuerdo, lo corrijo.",
        ),
        _p(
            "confirm_summary.repeat_request",
            "confirm_summary",
            "El resumen no era correcto: se vuelve a recoger la solicitud.",
            "De acuerdo. Cuénteme de nuevo qué ocurre, con más detalle si puede.",
        ),
        # ---------------------------------------------------------------- emergencias
        _p(
            "handle_emergency.notice",
            "handle_emergency",
            "Emergencia con el tratamiento especial activado: primero la indicación de "
            "seguridad ({seguridad}, una de las tres siguientes) y solo se confirma lo mínimo, "
            "la finca y que hay una emergencia. Sin detalle técnico ni resumen largo.",
            "{seguridad} Le confirmo: hay una emergencia en {address}. ¿Es así?",
            question=True,
            notice=True,
        ),
        _p(
            "handle_emergency.gas_safety",
            "handle_emergency",
            "Indicación de seguridad cuando el vecino habla de gas (olor, fuga, butano).",
            "Si huele a gas, no encienda luces ni aparatos ni haga llama; salga y, si es "
            "seguro, abra las ventanas. Si alguien corre peligro, llame al 112.",
        ),
        _p(
            "handle_emergency.fire_safety",
            "handle_emergency",
            "Indicación de seguridad cuando el vecino habla de fuego, humo o incendio.",
            "Si hay fuego o humo, salga por la escalera, sin usar el ascensor, y llame al 112.",
        ),
        _p(
            "handle_emergency.general_safety",
            "handle_emergency",
            "Indicación de seguridad para el resto de emergencias (alguien atrapado, derrumbe).",
            "Si alguien corre peligro, llame al 112 ahora mismo y no se ponga en riesgo.",
        ),
        # ---------------------------------------------------------------- cierres
        _p(
            "unclassifiable.farewell",
            "unclassifiable",
            "La solicitud no encaja en el catálogo: la revisará una persona.",
            "Esta solicitud escapa a lo que puedo gestionar automáticamente. He enviado un "
            "resumen al administrador para que la revise una persona. Gracias por su llamada.",
            notice=True,
        ),
        _p(
            "handoff_to_human.farewell",
            "handoff_to_human",
            "El vecino ha pedido hablar con una persona.",
            "De acuerdo. Envío ahora un resumen de su llamada al administrador para que una "
            "persona se ponga en contacto con usted lo antes posible. Gracias, hasta luego.",
            notice=True,
        ),
        _p(
            "farewell.farewell",
            "farewell",
            "El vecino se despide antes de terminar el guion.",
            "De acuerdo. Gracias por llamar; puede volver a llamar cuando quiera. Hasta luego.",
        ),
        # ---------------------------------------------------------------- avisar y despedir
        _p(
            "notify_channel.closing",
            "notify_channel",
            "Cierre con qué va a pasar a continuación. Sin la referencia: el administrador la "
            "tiene y al vecino no le sirve de nada oír un código.",
            "He registrado su incidencia. {notice} Si en veinticuatro "
            "horas nadie le ha contactado, vuelva a llamarnos. Gracias por su llamada, "
            "hasta luego.",
            notice=True,
        ),
        _p(
            "notify_channel.emergency_close",
            "notify_channel",
            "Cierre de una emergencia (tratamiento especial activado). No promete una llamada "
            "ya hecha: el aviso al teléfono oficial queda pendiente. Repite el 112.",
            "He registrado la emergencia y se la paso con prioridad al administrador de su "
            "comunidad. Si alguien corre peligro, llame al 112. Gracias por su llamada.",
            notice=True,
        ),
        _p(
            "notify_channel.no_specialist_notice",
            "notify_channel",
            "No hay profesional para la categoría: lo revisa el administrador.",
            "El administrador la revisará hoy mismo, le asignará el profesional adecuado y "
            "{contact} para concertar la visita.",
        ),
        _p(
            "notify_channel.coordinator_notice",
            "notify_channel",
            "Urgencia alta: se avisa al coordinador además del profesional. Nunca promete hora.",
            "Por su urgencia aviso ahora mismo al coordinador y a {specialist}. "
            "{specialist_capitalized} {contact} lo antes posible para decirle cuándo puede ir; "
            "todavía no puedo confirmarle la hora.",
        ),
        _p(
            "notify_channel.administrator_notice",
            "notify_channel",
            "No es una reparación que se pueda encargar directamente (vandalismo, obra, "
            "convivencia...) o el vecino puso una pega: decide el administrador.",
            "Se la paso al administrador, que la revisará hoy mismo y {contact} para decirle "
            "cómo se va a resolver.",
        ),
        _p(
            "notify_channel.specialist_notice",
            "notify_channel",
            "Caso normal: se comunica al profesional asignado.",
            "Se lo comunico ahora a {specialist}, que {contact} para concertar la visita. "
            "Todavía no puedo darle hora porque depende de su disponibilidad; el siguiente paso "
            "es su llamada.",
        ),
        _p(
            "notify_channel.phone_contact",
            "notify_channel",
            "Fragmento: hay teléfono del llamante, así que le devolverán la llamada.",
            "le llamará a este mismo número",
        ),
        _p(
            "notify_channel.generic_contact",
            "notify_channel",
            "Fragmento: no hay teléfono del llamante.",
            "se pondrá en contacto con usted",
        ),
    )
)


#: Texto efectivo por id. Arranca con el del código y se actualiza con las sobrescrituras.
_EFFECTIVE: dict[str, str] = {id_: p.text for id_, p in CATALOG.items()}
#: Versión efectiva por id. 0 = el texto del código, sin sobrescribir.
_VERSION: dict[str, int] = dict.fromkeys(CATALOG, 0)


def render(id_: str, **values: object) -> RenderedText:
    """Renderiza una plantilla. Solo diccionario y `str.format`: no toca la base de datos."""
    return RenderedText(_EFFECTIVE[id_].format(**values), id_, _VERSION[id_])


def effective_text(id_: str) -> str:
    return _EFFECTIVE[id_]


def effective_version(id_: str) -> int:
    return _VERSION[id_]


def is_overridden(id_: str) -> bool:
    return _VERSION[id_] > 0


class InvalidTemplate(ValueError):
    """El texto propuesto rompe el contrato de la plantilla."""


def validate(id_: str, text: str) -> list[str]:
    """Devuelve la lista de problemas del texto propuesto. Vacía significa que se puede publicar."""
    template = CATALOG[id_]
    problems: list[str] = []
    text = text.strip()

    if not text:
        return ["El texto no puede estar vacío."]

    found = set(_VARIABLE.findall(text))
    declared_vars = set(template.variables)
    missing = declared_vars - found
    extra = found - declared_vars
    if missing:
        problems.append(
            "Faltan variables que el nodo rellena y sin las cuales el mensaje pierde datos: "
            + ", ".join("{" + v + "}" for v in sorted(missing))
        )
    if extra:
        problems.append(
            "Hay variables que el nodo no rellena; la llamada fallaría al decir esta frase: "
            + ", ".join("{" + v + "}" for v in sorted(extra))
        )
    # El interrogante puede venir en el texto o en la variable que lo aporta
    is_question = "?" in text or (
        template.question_in is not None and "{" + template.question_in + "}" in text
    )
    if template.question and not is_question:
        problems.append(
            "Esta plantilla tiene que terminar preguntando: sin '?' la redacción natural se "
            "descarta siempre y el vecino oye el texto plano."
        )
    if "\n" in text:
        problems.append("Sin saltos de línea: esto se dice por teléfono.")
    if len(text) > MAX_CHARACTERS:
        problems.append(
            f"Demasiado largo ({len(text)} caracteres, máximo {MAX_CHARACTERS}). "
            "Por teléfono, frases cortas."
        )
    return problems


def apply(id_: str, text: str, version: int) -> None:
    """Publica una sobrescritura ya validada, en memoria."""
    problems = validate(id_, text)
    if problems:
        raise InvalidTemplate("; ".join(problems))
    _EFFECTIVE[id_] = text.strip()
    _VERSION[id_] = version


def restore(id_: str) -> None:
    """Vuelve al texto del código."""
    _EFFECTIVE[id_] = CATALOG[id_].text
    _VERSION[id_] = 0


def load(overrides: dict[str, tuple[str, int]]) -> None:
    """Carga el estado publicado. Se llama al arrancar y después de cada guardado."""
    for id_ in CATALOG:
        restore(id_)
    for id_, (text, version) in overrides.items():
        if id_ in CATALOG:
            _EFFECTIVE[id_] = text
            _VERSION[id_] = version
