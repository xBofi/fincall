"""Manifiesto del grafo: la estructura que el código compilado no puede contar.

`registro.py` conecta todos los nodos con todos y deja que `_siguiente` lea `nodo_actual`, así
que el grafo compilado de LangGraph no dice nada del flujo real. Este módulo declara, por nodo,
qué hace y a dónde puede saltar, y `nodos.py` lo verifica en cada transición: una arista que no
esté aquí declarada rompe la suite (`BC_GRAFO_ESTRICTO=1`) y queda registrada en producción.

No es documentación decorativa. Si alguien añade una transición y no la declara, los tests fallan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

FIN = "fin"

TipoNodo = Literal["decision", "pregunta", "efecto", "terminal"]


@dataclass(frozen=True)
class Arista:
    destino: str
    condicion: str


@dataclass(frozen=True)
class NodoManifiesto:
    id: str
    titulo: str
    resumen: str
    tipo: TipoNodo
    #: Si el nodo puede provocar una llamada al modelo en su turno
    usa_llm: bool = False
    #: Atajos que se resuelven en código, sin modelo (turnos de ~0 ms)
    caminos_deterministas: tuple[str, ...] = ()
    #: Efectos con consecuencias fuera del estado (BD, avisos)
    efectos: tuple[str, ...] = ()
    destinos: tuple[Arista, ...] = ()
    #: Ids del catálogo de plantillas que este nodo puede emitir
    plantillas: tuple[str, ...] = field(default_factory=tuple)
    #: Etapa de la conversación, para dibujarlo. No influye en el flujo: es la pista de
    #: disposición, y vive aquí para que el dibujo no duplique el conocimiento del flujo.
    etapa: int = 0


# Los nodos que consumen un turno del vecino pasan por `_turno`, que atiende las intenciones
# globales: repetir y preguntar/fuera de ámbito vuelven al propio nodo, y `humano` y `colgar`
# (solo con palabras explícitas del vecino) derivan o se despiden.
_INTENCIONES_GLOBALES = (
    Arista("derivar_humano", "el vecino pide expresamente hablar con una persona"),
    Arista("despedida", "el vecino se despide expresamente sin aportar datos"),
)

_DETERMINISTAS_TURNO = (
    "no se ha oído (se repite el mensaje completo)",
    'relleno ("un momento", "a ver...")',
)


MANIFIESTO: dict[str, NodoManifiesto] = {
    "siguiente_paso": NodoManifiesto(
        id="siguiente_paso",
        etapa=0,
        titulo="Siguiente paso",
        resumen=(
            "El único nodo que decide el orden de las preguntas. Consume lo que el vecino ya haya "
            "dicho (aunque no fuera lo que se le preguntaba) y salta a lo primero que falte: "
            "finca, vivienda, solicitud, categoría, urgencia, detalle o confirmación. "
            "Los nodos no se encadenan entre sí: todos vuelven aquí."
        ),
        tipo="decision",
        usa_llm=False,
        caminos_deterministas=(
            "cotejo de dirección por similitud (umbral 0,6)",
            "búsqueda de vivienda por portal/piso/puerta",
        ),
        destinos=(
            Arista("identificar_finca", "no hay finca, o hay una candidata que confirmar"),
            Arista("finca_no_identificada", "agotados los intentos de identificar la finca"),
            Arista("identificar_vivienda", "hay finca pero no vivienda, o falta un dato de ella"),
            Arista("vivienda_no_identificada", "agotados los intentos de identificar la vivienda"),
            Arista("recoger_solicitud", "falta saber qué le ocurre"),
            Arista("clasificar_problema", "hay solicitud pero no categoría"),
            Arista("determinar_urgencia", "hay categoría pero no urgencia"),
            Arista(
                "atender_emergencia",
                "urgencia efectiva `emergencia` (tratamiento activado) sin confirmar todavía",
            ),
            Arista("pedir_detalle", "la configuración pide un detalle y la incidencia lo merece"),
            Arista("confirmar_resumen", "están los datos y falta que el vecino confirme"),
            Arista("validar_datos", "el vecino ya ha confirmado el resumen"),
        ),
    ),
    "identificar_finca": NodoManifiesto(
        id="identificar_finca",
        etapa=1,
        titulo="Identificar finca",
        resumen=(
            "Pregunta la calle y el número, o pide confirmación de la finca candidata que "
            "`siguiente_paso` encontró por similitud. Consume intentos: al agotarlos, deriva."
        ),
        tipo="pregunta",
        usa_llm=True,
        caminos_deterministas=("sí / no", "saludo inicial", *_DETERMINISTAS_TURNO),
        destinos=(
            Arista("identificar_finca", "repregunta: no confirmada, no entendida o reintento"),
            Arista("siguiente_paso", "finca confirmada"),
            Arista("finca_no_identificada", "agotados los intentos"),
            *_INTENCIONES_GLOBALES,
        ),
    ),
    "finca_no_identificada": NodoManifiesto(
        id="finca_no_identificada",
        etapa=1,
        titulo="Finca no identificada",
        resumen=(
            "Salida digna cuando la finca no consta: recoge lo que el vecino quería comunicar y "
            "deja la llamada registrada para que calidad la revise. No crea incidencia."
        ),
        tipo="efecto",
        usa_llm=False,
        efectos=("guarda la llamada con resultado `finca_no_identificada`",),
        destinos=(
            Arista("finca_no_identificada", "primera pasada: pide que cuente qué quería"),
            Arista(FIN, "recogido el mensaje, se cierra la llamada"),
        ),
    ),
    "identificar_vivienda": NodoManifiesto(
        id="identificar_vivienda",
        etapa=2,
        titulo="Identificar vivienda",
        resumen=(
            "Pregunta portal, piso y puerta y los coteja contra las viviendas de la finca. "
            "Si el piso y la puerta existen en varios portales, pregunta solo el portal."
        ),
        tipo="pregunta",
        usa_llm=True,
        caminos_deterministas=(
            '"3º B" y variantes por expresión regular',
            *_DETERMINISTAS_TURNO,
        ),
        destinos=(
            Arista("identificar_vivienda", "repregunta: falta un dato o no coincide"),
            Arista("siguiente_paso", "vivienda identificada sin ambigüedad"),
            Arista("vivienda_no_identificada", "agotados los intentos"),
            *_INTENCIONES_GLOBALES,
        ),
    ),
    "vivienda_no_identificada": NodoManifiesto(
        id="vivienda_no_identificada",
        etapa=2,
        titulo="Vivienda no identificada",
        resumen=(
            "Recoge la solicitud aunque no se sepa la vivienda y avisa al administrador por "
            "correo con el teléfono del llamante. No crea incidencia ni asigna profesional."
        ),
        tipo="efecto",
        usa_llm=False,
        efectos=(
            "guarda la llamada con resultado `vivienda_no_identificada`",
            "avisa al administrador por correo",
        ),
        destinos=(
            Arista("vivienda_no_identificada", "primera pasada: pide la solicitud igualmente"),
            Arista(FIN, "recogida la solicitud, se cierra la llamada"),
        ),
    ),
    "recoger_solicitud": NodoManifiesto(
        id="recoger_solicitud",
        etapa=3,
        titulo="Recoger solicitud",
        resumen=(
            "Pregunta qué ocurre. La interpretación del turno clasifica en la misma llamada al "
            "modelo (categoría y urgencia), para no gastar un segundo turno en clasificar."
        ),
        tipo="pregunta",
        usa_llm=True,
        caminos_deterministas=("FAQ (¿esto se graba?, ¿cuánto tardan?)", *_DETERMINISTAS_TURNO),
        destinos=(
            Arista("recoger_solicitud", "no ha contado todavía qué le ocurre"),
            Arista("siguiente_paso", "solicitud recogida"),
            *_INTENCIONES_GLOBALES,
        ),
    ),
    "clasificar_problema": NodoManifiesto(
        id="clasificar_problema",
        etapa=3,
        titulo="Clasificar problema",
        resumen=(
            "Respaldo: solo se ejecuta si la interpretación del turno no trajo ya la categoría. "
            "Valida contra el catálogo de la finca; lo que no encaje cae en `no_clasificable`."
        ),
        tipo="decision",
        usa_llm=True,
        destinos=(Arista("determinar_urgencia", "clasificado, con categoría y urgencia"),),
    ),
    "pedir_detalle": NodoManifiesto(
        id="pedir_detalle",
        etapa=4,
        titulo="Pedir detalle",
        resumen=(
            "Una sola pregunta de detalle antes del resumen, para que el profesional sepa a qué "
            "va. Se puede desactivar por finca (`pedir_detalle`) y solo se hace si el caso lo merece."
        ),
        tipo="pregunta",
        usa_llm=True,
        caminos_deterministas=_DETERMINISTAS_TURNO,
        destinos=(
            Arista("pedir_detalle", "no ha respondido al detalle"),
            Arista("siguiente_paso", "detalle recogido, o el vecino no lo aporta"),
            *_INTENCIONES_GLOBALES,
        ),
    ),
    "determinar_urgencia": NodoManifiesto(
        id="determinar_urgencia",
        etapa=3,
        titulo="Determinar urgencia",
        resumen=(
            "Convierte la urgencia declarada en la efectiva según la configuración. Mientras las "
            "emergencias estén desactivadas (`emergencias_activas`), `emergencia` se trata como "
            "`alta`; activadas, `siguiente_paso` la lleva a `atender_emergencia`."
        ),
        tipo="decision",
        usa_llm=False,
        destinos=(Arista("siguiente_paso", "urgencia efectiva fijada"),),
    ),
    "atender_emergencia": NodoManifiesto(
        id="atender_emergencia",
        etapa=5,
        titulo="Atender emergencia",
        resumen=(
            "Solo con `emergencias_activas`. Da primero una indicación de seguridad (112; con gas, "
            "no encender luces ni aparatos y salir) y confirma lo mínimo: la finca y que hay una "
            "emergencia. No pide detalle ni lee el resumen largo. Si el vecino dice que no es una "
            "emergencia, sigue como urgencia alta; ante la duda, se registra igualmente."
        ),
        tipo="pregunta",
        usa_llm=True,
        caminos_deterministas=(
            "indicación de seguridad por palabras clave (gas, fuego/humo, resto)",
            "sí / no",
            *_DETERMINISTAS_TURNO,
        ),
        destinos=(
            Arista("atender_emergencia", "el vecino pide repetir o hace una pregunta"),
            Arista(
                "siguiente_paso",
                "confirmada (o respuesta dudosa: se registra igual), o no es una emergencia",
            ),
            *_INTENCIONES_GLOBALES,
        ),
    ),
    "confirmar_resumen": NodoManifiesto(
        id="confirmar_resumen",
        etapa=5,
        titulo="Confirmar resumen",
        resumen=(
            "Lee al vecino quién es, qué ocurre y con qué urgencia, y espera un sí o un no. "
            "Un no con corrección vuelve a recoger la solicitud."
        ),
        tipo="pregunta",
        usa_llm=True,
        caminos_deterministas=("sí / no", *_DETERMINISTAS_TURNO),
        destinos=(
            Arista("confirmar_resumen", "no se ha entendido la respuesta"),
            Arista("siguiente_paso", "confirmado, o hay una corrección que reprocesar"),
            Arista("validar_datos", "confirmado y sin nada que corregir"),
            Arista("recoger_solicitud", "el vecino dice que el resumen no es correcto"),
            *_INTENCIONES_GLOBALES,
        ),
    ),
    "validar_datos": NodoManifiesto(
        id="validar_datos",
        etapa=6,
        titulo="Validar datos",
        resumen=(
            "Última comprobación antes de tocar la base de datos: que haya finca, vivienda, "
            "categoría dentro del catálogo y urgencia válida."
        ),
        tipo="decision",
        usa_llm=False,
        destinos=(
            Arista("no_clasificable", "la categoría no encaja en el catálogo de la finca"),
            Arista("asignar_especialista", "datos completos y coherentes"),
        ),
    ),
    "no_clasificable": NodoManifiesto(
        id="no_clasificable",
        etapa=8,
        titulo="No clasificable",
        resumen=(
            "La solicitud no encaja en ninguna categoría del catálogo: queda para el "
            "administrador en vez de asignarse a un profesional equivocado."
        ),
        tipo="efecto",
        usa_llm=False,
        efectos=("guarda la llamada con resultado `no_clasificable`",),
        destinos=(Arista(FIN, "se despide tras dejar constancia"),),
    ),
    "derivar_humano": NodoManifiesto(
        id="derivar_humano",
        etapa=8,
        titulo="Derivar a humano",
        resumen=(
            "El vecino ha pedido expresamente hablar con una persona. Exige palabras explícitas, "
            "no solo la opinión del modelo: derivar es irreversible."
        ),
        tipo="terminal",
        usa_llm=False,
        efectos=("guarda la llamada con resultado `derivada`",),
        destinos=(Arista(FIN, "derivada"),),
    ),
    "despedida": NodoManifiesto(
        id="despedida",
        etapa=8,
        titulo="Despedida",
        resumen="El vecino se despide antes de terminar el guion. Se cierra sin incidencia.",
        tipo="terminal",
        usa_llm=False,
        efectos=("guarda la llamada",),
        destinos=(Arista(FIN, "colgada por el vecino"),),
    ),
    "asignar_especialista": NodoManifiesto(
        id="asignar_especialista",
        etapa=7,
        titulo="Asignar especialista",
        resumen=(
            "Busca el profesional de la categoría en la configuración efectiva de la finca y "
            "comprueba que pertenece a su administrador. Sin profesional, queda pendiente."
        ),
        tipo="efecto",
        usa_llm=False,
        efectos=("selecciona profesional, o marca la incidencia como pendiente de administrador",),
        destinos=(Arista("crear_ticket", "con profesional asignado o sin él"),),
    ),
    "crear_ticket": NodoManifiesto(
        id="crear_ticket",
        etapa=7,
        titulo="Crear incidencia",
        resumen="Persiste la llamada y la incidencia. Es el punto sin retorno del flujo.",
        tipo="efecto",
        usa_llm=False,
        efectos=("guarda la llamada", "crea la incidencia"),
        destinos=(Arista("avisar_canal", "incidencia creada"),),
    ),
    "avisar_canal": NodoManifiesto(
        id="avisar_canal",
        etapa=7,
        titulo="Avisar y despedir",
        resumen=(
            "Aplica la regla de aviso de la urgencia (canal y destino) y le cuenta al vecino qué "
            "va a pasar y quién le llamará. Nunca promete una hora: nadie ha hablado aún con el "
            "profesional."
        ),
        tipo="efecto",
        usa_llm=False,
        efectos=(
            "avisa por el canal de la regla (ticket, correo o llamada)",
            "emergencia: deja simulada la llamada al teléfono oficial y el correo al coordinador",
        ),
        destinos=(Arista(FIN, "incidencia creada y vecino informado"),),
    ),
}


#: Destinos declarados por nodo, para la verificación en ejecución.
TRANSICIONES: dict[str, frozenset[str]] = {
    id_: frozenset(a.destino for a in n.destinos) for id_, n in MANIFIESTO.items()
}


class TransicionNoDeclarada(RuntimeError):
    """Un nodo ha saltado a un destino que el manifiesto no declara."""


def estricto() -> bool:
    """En tests y con `BC_GRAFO_ESTRICTO=1` una transición no declarada es un error."""
    return os.getenv("BC_GRAFO_ESTRICTO") == "1" or os.getenv("PYTEST_CURRENT_TEST") is not None


def verificar_transicion(origen: str | None, destino: str) -> str | None:
    """Devuelve el motivo de la violación, o None si la transición está declarada.

    En producción nunca lanza: una arista mal declarada no puede colgarle el teléfono a un vecino.
    """
    if origen is None or origen not in TRANSICIONES:
        return None
    if destino in TRANSICIONES[origen]:
        return None
    motivo = f"{origen} -> {destino} no está declarada en el manifiesto"
    if estricto():
        raise TransicionNoDeclarada(motivo)
    return motivo
