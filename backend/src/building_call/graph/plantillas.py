"""Catálogo de lo que el asistente dice, editable desde la consola.

Un nodo no tiene "un prompt": tiene **plantillas**, texto con datos interpolados. Esa plantilla
es el MENSAJE OBLIGATORIO que `llm/redaccion.py` parafrasea conservando los hechos. Los tres
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
MAX_CARACTERES = 320

_VARIABLE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Plantilla:
    id: str
    nodo: str
    descripcion: str
    texto: str
    variables: tuple[str, ...] = ()
    #: Si el mensaje debe terminar preguntando. `conserva_hechos()` descarta la paráfrasis que
    #: pierde el interrogante, así que una plantilla sin `?` deja la redacción inservible.
    pregunta: bool = False
    #: Nombre de la variable que aporta el interrogante, cuando la pregunta no está en el texto
    #: sino en un dato que el nodo inyecta (por ejemplo, la pregunta anterior que se recupera).
    pregunta_en: str | None = None


class TextoRenderizado(str):
    """El texto ya renderizado, que recuerda de qué plantilla y versión salió.

    Es un `str` normal a todos los efectos: se puede pasar donde se pasaba el literal. Lleva
    la identidad encima para que la auditoría no dependa de cablear el id por cada firma.
    """

    __slots__ = ("plantilla_id", "version")

    def __new__(cls, texto: str, plantilla_id: str, version: int) -> TextoRenderizado:
        obj = super().__new__(cls, texto)
        obj.plantilla_id = plantilla_id  # type: ignore[misc]
        obj.version = version  # type: ignore[misc]
        return obj


def _p(
    id_: str,
    nodo: str,
    descripcion: str,
    texto: str,
    *,
    pregunta: bool = False,
    pregunta_en: str | None = None,
) -> tuple[str, Plantilla]:
    variables = tuple(dict.fromkeys(_VARIABLE.findall(texto)))
    assert pregunta_en is None or pregunta_en in variables, id_
    return id_, Plantilla(
        id=id_,
        nodo=nodo,
        descripcion=descripcion,
        texto=texto,
        variables=variables,
        pregunta=pregunta or pregunta_en is not None,
        pregunta_en=pregunta_en,
    )


CATALOGO: dict[str, Plantilla] = dict(
    (
        # ---------------------------------------------------------------- intenciones globales
        _p(
            "global.repetir",
            "global",
            "El vecino pide que se le repita, desde cualquier punto de la llamada.",
            "Claro, le repito. {mensaje}",
        ),
        _p(
            "global.no_oido",
            "global",
            "Se detecta que el vecino no ha oído el mensaje (camino determinista, sin modelo).",
            "Perdone, le repito. {mensaje}",
        ),
        _p(
            "global.relleno",
            "global",
            'El vecino pide tiempo ("un momento", "a ver..."). No cuenta como intento fallido.',
            "Claro, sin prisa. Dígame cuando quiera.",
        ),
        _p(
            "global.fuera_ambito",
            "global",
            "El vecino pide algo ajeno a incidencias de la finca (cuotas, juntas, otros vecinos).",
            "Lo siento, solo puedo gestionar incidencias y solicitudes de la finca. {pregunta}",
            pregunta_en="pregunta",
        ),
        # ---------------------------------------------------------------- identificar finca
        _p(
            "identificar_finca.saludo",
            "identificar_finca",
            "Primer mensaje de la llamada.",
            "Hola, soy el asistente de incidencias de su comunidad. ¿En qué finca se encuentra? "
            "Dígame la calle y el número.",
            pregunta=True,
        ),
        _p(
            "identificar_finca.saludo_respuesta",
            "identificar_finca",
            "El vecino solo ha saludado, sin dar la dirección.",
            "Hola, buenas. ¿En qué finca se encuentra? Dígame la calle y el número.",
            pregunta=True,
        ),
        _p(
            "identificar_finca.candidata",
            "identificar_finca",
            "Se ha encontrado una finca parecida a lo que dijo y hay que confirmarla.",
            "He identificado {direccion}. ¿Es correcto?",
            pregunta=True,
        ),
        _p(
            "identificar_finca.confirmar_si_no",
            "identificar_finca",
            "No se ha entendido si confirmaba o no la finca candidata.",
            "Perdone, ¿es correcta esa finca? Responda sí o no.",
            pregunta=True,
        ),
        _p(
            "identificar_finca.reintento",
            "identificar_finca",
            "Reintento tras no encontrar o no entender la dirección. {motivo} explica por qué.",
            "{motivo} ¿Puede repetirme la calle y el número?",
            pregunta=True,
        ),
        _p(
            "identificar_finca.motivo_no_encontrada",
            "identificar_finca",
            "Motivo del reintento: la dirección no está entre las fincas gestionadas.",
            "No he encontrado esa dirección entre las fincas gestionadas.",
        ),
        _p(
            "identificar_finca.motivo_no_entendida",
            "identificar_finca",
            "Motivo del reintento: no se ha entendido la dirección.",
            "No he entendido la dirección de la finca.",
        ),
        _p(
            "identificar_finca.motivo_no_es_esa",
            "identificar_finca",
            "Motivo del reintento: el vecino ha dicho que la finca candidata no es la suya.",
            "Entendido, no es esa.",
        ),
        # ---------------------------------------------------------------- finca no identificada
        _p(
            "finca_no_identificada.recoger",
            "finca_no_identificada",
            "No consta la finca: se recoge igualmente el mensaje del vecino.",
            "Lo siento, no consigo identificar la finca; puede que no esté gestionada por este "
            "administrador. Si quiere, cuénteme brevemente qué quería comunicar y quedará "
            "registrado.",
        ),
        _p(
            "finca_no_identificada.despedida",
            "finca_no_identificada",
            "Cierre tras registrar la llamada para que la revise calidad.",
            "Gracias. He registrado su llamada para que el equipo de calidad la revise. "
            "Hasta luego.",
        ),
        # ---------------------------------------------------------------- identificar vivienda
        _p(
            "identificar_vivienda.preguntar",
            "identificar_vivienda",
            "Primera pregunta por la vivienda, ya con la finca confirmada.",
            "Gracias. ¿Cuál es su vivienda? Indíqueme portal si lo hay, piso y puerta.",
            pregunta=True,
        ),
        _p(
            "identificar_vivienda.parcial",
            "identificar_vivienda",
            "El vecino ha dado parte del dato; se conserva y se pide solo lo que falta.",
            "Tomo nota: {tiene}. ¿Y {falta}?",
            pregunta=True,
        ),
        _p(
            "identificar_vivienda.varios_portales",
            "identificar_vivienda",
            "Ese piso y puerta existen en varios portales: solo falta el portal.",
            "El {piso}º {puerta} existe en varios portales. ¿En qué portal vive, {portales}?",
            pregunta=True,
        ),
        _p(
            "identificar_vivienda.reintento",
            "identificar_vivienda",
            "La vivienda no existe en la finca. {ayuda} describe las viviendas que sí existen.",
            "No encuentro esa vivienda. {ayuda}¿Cuál es su piso y su puerta?",
            pregunta=True,
        ),
        _p(
            "identificar_vivienda.repregunta",
            "identificar_vivienda",
            "Respaldo cuando no hay una pregunta anterior que repetir.",
            "¿Cuál es su piso y su puerta?",
            pregunta=True,
        ),
        # ------------------------------------------------------------- vivienda no identificada
        _p(
            "vivienda_no_identificada.recoger",
            "vivienda_no_identificada",
            "No se ha podido identificar la vivienda: se recoge la solicitud igualmente.",
            "No he podido identificar correctamente la vivienda. Cuénteme igualmente su "
            "solicitud; enviaré un resumen al administrador con su número de teléfono.",
        ),
        _p(
            "vivienda_no_identificada.despedida",
            "vivienda_no_identificada",
            "Cierre tras avisar al administrador por correo.",
            "Gracias. He enviado el resumen al administrador, que se pondrá en contacto con "
            "usted. Hasta luego.",
        ),
        # ---------------------------------------------------------------- registrar nombre
        _p(
            "registrar_nombre.preguntar",
            "registrar_nombre",
            "Se pregunta el nombre, confirmando de paso la vivienda identificada.",
            "Perfecto, {vivienda}. ¿Con quién hablo? Dígame su nombre.",
            pregunta=True,
        ),
        _p(
            "registrar_nombre.repregunta",
            "registrar_nombre",
            "No se ha entendido el nombre.",
            "Perdone, no he entendido su nombre. ¿Me lo puede repetir?",
            pregunta=True,
        ),
        # ---------------------------------------------------------------- recoger solicitud
        _p(
            "recoger_solicitud.preguntar",
            "recoger_solicitud",
            "Se pregunta qué ocurre. {saludo} lleva el nombre del vecino si se conoce.",
            "{saludo}Cuénteme, ¿qué ocurre?",
            pregunta=True,
        ),
        _p(
            "recoger_solicitud.saludo_con_nombre",
            "recoger_solicitud",
            "Prefijo con el nombre del vecino para la pregunta de solicitud.",
            "Gracias, {nombre}. ",
        ),
        # ---------------------------------------------------------------- confirmar resumen
        _p(
            "confirmar_resumen.resumen",
            "confirmar_resumen",
            "Se le lee al vecino lo entendido y se espera un sí o un no.",
            "Le resumo: {quien}. {resumen}. {categoria}, con urgencia {urgencia}. ¿Es correcto?",
            pregunta=True,
        ),
        _p(
            "confirmar_resumen.categoria",
            "confirmar_resumen",
            "Fragmento con la categoría, dentro del resumen.",
            "lo he clasificado como {categoria}",
        ),
        _p(
            "confirmar_resumen.categoria_no_clasificable",
            "confirmar_resumen",
            "Fragmento cuando no encaja en el catálogo. Nunca se le dice al vecino que no se "
            "ha podido clasificar: se registra igual.",
            "lo registro como solicitud general para el administrador",
        ),
        _p(
            "confirmar_resumen.confirmar_si_no",
            "confirmar_resumen",
            "No se ha entendido si confirmaba el resumen.",
            "Perdone, no le he entendido. ¿Es correcto el resumen? Responda sí o no.",
            pregunta=True,
        ),
        _p(
            "confirmar_resumen.corrigiendo",
            "confirmar_resumen",
            "El vecino ha corregido un dato y se toma nota.",
            "De acuerdo, lo corrijo.",
        ),
        _p(
            "confirmar_resumen.repetir_solicitud",
            "confirmar_resumen",
            "El resumen no era correcto: se vuelve a recoger la solicitud.",
            "De acuerdo. Cuénteme de nuevo qué ocurre, con más detalle si puede.",
        ),
        # ---------------------------------------------------------------- cierres
        _p(
            "no_clasificable.despedida",
            "no_clasificable",
            "La solicitud no encaja en el catálogo: la revisará una persona.",
            "Esta solicitud escapa a lo que puedo gestionar automáticamente. He enviado un "
            "resumen al administrador para que la revise una persona. Gracias por su llamada.",
        ),
        _p(
            "derivar_humano.despedida",
            "derivar_humano",
            "El vecino ha pedido hablar con una persona.",
            "De acuerdo. Envío ahora un resumen de su llamada al administrador para que una "
            "persona se ponga en contacto con usted lo antes posible. Gracias, hasta luego.",
        ),
        _p(
            "despedida.despedida",
            "despedida",
            "El vecino se despide antes de terminar el guion.",
            "De acuerdo. Gracias por llamar; puede volver a llamar cuando quiera. Hasta luego.",
        ),
        # ---------------------------------------------------------------- avisar y despedir
        _p(
            "avisar_canal.cierre",
            "avisar_canal",
            "Cierre con la referencia de la incidencia y qué va a pasar a continuación.",
            "He registrado su incidencia con referencia {referencia}. {aviso} Si en veinticuatro "
            "horas nadie le ha contactado, vuelva a llamarnos. Gracias por su llamada, "
            "hasta luego.",
        ),
        _p(
            "avisar_canal.aviso_sin_especialista",
            "avisar_canal",
            "No hay profesional para la categoría: lo revisa el administrador.",
            "El administrador la revisará hoy mismo, le asignará el profesional adecuado y "
            "{contacto} para concertar la visita.",
        ),
        _p(
            "avisar_canal.aviso_coordinador",
            "avisar_canal",
            "Urgencia alta: se avisa al coordinador además del profesional. Nunca promete hora.",
            "Por su urgencia aviso ahora mismo al coordinador y a {especialista}. "
            "{especialista_mayus} {contacto} lo antes posible para decirle cuándo puede ir; "
            "todavía no puedo confirmarle la hora.",
        ),
        _p(
            "avisar_canal.aviso_especialista",
            "avisar_canal",
            "Caso normal: se comunica al profesional asignado.",
            "Se lo comunico ahora a {especialista}, que {contacto} para concertar la visita. "
            "Todavía no puedo darle hora porque depende de su disponibilidad; el siguiente paso "
            "es su llamada.",
        ),
        _p(
            "avisar_canal.contacto_telefono",
            "avisar_canal",
            "Fragmento: hay teléfono del llamante, así que le devolverán la llamada.",
            "le llamará a este mismo número",
        ),
        _p(
            "avisar_canal.contacto_generico",
            "avisar_canal",
            "Fragmento: no hay teléfono del llamante.",
            "se pondrá en contacto con usted",
        ),
    )
)


#: Texto efectivo por id. Arranca con el del código y se actualiza con las sobrescrituras.
_EFECTIVO: dict[str, str] = {id_: p.texto for id_, p in CATALOGO.items()}
#: Versión efectiva por id. 0 = el texto del código, sin sobrescribir.
_VERSION: dict[str, int] = dict.fromkeys(CATALOGO, 0)


def render(id_: str, **valores: object) -> TextoRenderizado:
    """Renderiza una plantilla. Solo diccionario y `str.format`: no toca la base de datos."""
    return TextoRenderizado(_EFECTIVO[id_].format(**valores), id_, _VERSION[id_])


def texto_efectivo(id_: str) -> str:
    return _EFECTIVO[id_]


def version_efectiva(id_: str) -> int:
    return _VERSION[id_]


def esta_sobrescrita(id_: str) -> bool:
    return _VERSION[id_] > 0


class PlantillaInvalida(ValueError):
    """El texto propuesto rompe el contrato de la plantilla."""


def validar(id_: str, texto: str) -> list[str]:
    """Devuelve la lista de problemas del texto propuesto. Vacía significa que se puede publicar."""
    plantilla = CATALOGO[id_]
    problemas: list[str] = []
    texto = texto.strip()

    if not texto:
        return ["El texto no puede estar vacío."]

    encontradas = set(_VARIABLE.findall(texto))
    declaradas = set(plantilla.variables)
    faltan = declaradas - encontradas
    sobran = encontradas - declaradas
    if faltan:
        problemas.append(
            "Faltan variables que el nodo rellena y sin las cuales el mensaje pierde datos: "
            + ", ".join("{" + v + "}" for v in sorted(faltan))
        )
    if sobran:
        problemas.append(
            "Hay variables que el nodo no rellena; la llamada fallaría al decir esta frase: "
            + ", ".join("{" + v + "}" for v in sorted(sobran))
        )
    # El interrogante puede venir en el texto o en la variable que lo aporta
    interroga = "?" in texto or (
        plantilla.pregunta_en is not None and "{" + plantilla.pregunta_en + "}" in texto
    )
    if plantilla.pregunta and not interroga:
        problemas.append(
            "Esta plantilla tiene que terminar preguntando: sin '?' la redacción natural se "
            "descarta siempre y el vecino oye el texto plano."
        )
    if "\n" in texto:
        problemas.append("Sin saltos de línea: esto se dice por teléfono.")
    if len(texto) > MAX_CARACTERES:
        problemas.append(
            f"Demasiado largo ({len(texto)} caracteres, máximo {MAX_CARACTERES}). "
            "Por teléfono, frases cortas."
        )
    return problemas


def aplicar(id_: str, texto: str, version: int) -> None:
    """Publica una sobrescritura ya validada, en memoria."""
    problemas = validar(id_, texto)
    if problemas:
        raise PlantillaInvalida("; ".join(problemas))
    _EFECTIVO[id_] = texto.strip()
    _VERSION[id_] = version


def restaurar(id_: str) -> None:
    """Vuelve al texto del código."""
    _EFECTIVO[id_] = CATALOGO[id_].texto
    _VERSION[id_] = 0


def cargar(sobrescrituras: dict[str, tuple[str, int]]) -> None:
    """Carga el estado publicado. Se llama al arrancar y después de cada guardado."""
    for id_ in CATALOGO:
        restaurar(id_)
    for id_, (texto, version) in sobrescrituras.items():
        if id_ in CATALOGO:
            _EFECTIVO[id_] = texto
            _VERSION[id_] = version


def hechos_declarados(id_: str) -> tuple[str, ...]:
    """Las variables que el nodo inyecta, para enseñarlas en el editor."""
    return CATALOGO[id_].variables
