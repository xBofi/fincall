"""Nodos del flujo de llamada.

Reglas (bóveda, 04 Arquitectura): el LLM interpreta y extrae; el código valida y decide.
Cada nodo es una función reutilizable por cualquier grafo del registro.

Naturalidad: cada turno del vecino pasa por una interpretación (determinista si basta, LLM si
no) que extrae todo lo que dijo y una intención global. `siguiente_paso` consume esos datos y
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

from building_call.domain.configuracion import (
    CATEGORIA_NO_CLASIFICABLE,
    DEFAULTS,
    URGENCIAS,
    ConfiguracionEfectiva,
)
from building_call.domain.entidades import Incidencia, Llamada, Vivienda
from building_call.graph.estado import EstadoLlamada, Traza
from building_call.graph.manifiesto import verificar_transicion
from building_call.graph.plantillas import render
from building_call.llm.base import ModeloLenguaje
from building_call.llm.interpretacion import (
    VERSION_INTERPRETACION,
    Interpretacion,
    interpretar,
)
from building_call.llm.prompts import (
    VERSION_CLASIFICACION,
    esquema_clasificacion,
    sistema_clasificacion,
)
from building_call.repos.base import Repositorio

logger = logging.getLogger(__name__)

UMBRAL_FINCA = 0.6  # Pendiente: calibrar con datos reales (bóveda, 02 Flujos)


@dataclass
class Dependencias:
    repo: Repositorio
    llm: ModeloLenguaje
    # Interpretaciones lanzadas sobre transcripts parciales (antes del fin de turno): por llamada
    # y, dentro, por clave normalizada del texto. `_interpretar` las reutiliza si el final
    # coincide con el parcial. Separadas por llamada: con varias a la vez, una no puede cancelar
    # ni reutilizar lo que dijo el vecino de otra.
    especulaciones: dict[str, dict[str, asyncio.Future[Interpretacion]]] = field(
        default_factory=dict
    )

    def descartar_especulaciones(self, llamada_id: str) -> None:
        for futuro in self.especulaciones.pop(llamada_id, {}).values():
            futuro.cancel()


def clave_especulacion(texto: str) -> str:
    """Clave para casar un transcript parcial con el final (sin puntuación ni mayúsculas)."""
    return _normalizar_basico(texto)


# Cómo se dice cada categoría en voz alta (los códigos van sin tildes)
_CATEGORIA_HABLADA = {
    "fontaneria": "fontanería",
    "cerrajeria": "cerrajería",
    "albanileria": "albañilería",
    "jardineria": "jardinería",
    "administrativo": "gestión administrativa",
}


def categoria_hablada(categoria: str) -> str:
    return _CATEGORIA_HABLADA.get(categoria, categoria.replace("_", " "))


def _mayuscula_inicial(texto: str) -> str:
    """Como `str.capitalize` pero sin pasar a minúsculas el resto ("Fontanería Pérez")."""
    return texto[:1].upper() + texto[1:]


def catalogo_para(estado: EstadoLlamada) -> list[str]:
    return _catalogo(estado)


# --------------------------------------------------------------------------- normalización


def _nuevo_id(prefijo: str) -> str:
    return f"{prefijo}-{uuid4().hex[:12]}"


def _normalizar_basico(texto: str) -> str:
    """Minúsculas, sin acentos ni signos. No elimina palabras."""
    # º/ª se descomponen en "o"/"a" con NFKD ("3º" -> "3o"); se eliminan antes.
    texto = re.sub(r"[ºª°]", " ", texto.lower())
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


_NUMEROS = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100,
}  # fmt: skip


def _numeros_a_cifras(texto: str) -> str:
    """'treinta y dos' -> '32', 'doce' -> '12'. El STT puede dictar números en letra."""
    tokens = texto.split()
    salida: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _NUMEROS:
            valor = _NUMEROS[t]
            if (
                valor >= 30
                and valor % 10 == 0
                and i + 2 < len(tokens)
                and tokens[i + 1] == "y"
                and tokens[i + 2] in _NUMEROS
                and _NUMEROS[tokens[i + 2]] < 10
            ):
                valor += _NUMEROS[tokens[i + 2]]
                i += 2
            salida.append(str(valor))
        else:
            salida.append(t)
        i += 1
    return " ".join(salida)


def _normalizar(texto: str) -> str:
    """Normalización para direcciones: números en letra a cifras y sin palabras vacías."""
    texto = _numeros_a_cifras(_normalizar_basico(texto))
    texto = re.sub(
        r"\b(calle|c|avenida|avda|av|plaza|pza|paseo|numero|n|del|de|la|el|los|las|en|vivo|llamo|desde|es)\b",
        " ",
        texto,
    )
    return re.sub(r"\s+", " ", texto).strip()


def _similitud_direccion(entrada: str, direccion: str) -> float:
    a, b = _normalizar(entrada), _normalizar(direccion)
    if not a or not b:
        return 0.0
    tokens_b = set(b.split())
    contenidos = sum(1 for t in tokens_b if t in a.split())
    cobertura = contenidos / len(tokens_b)
    return max(SequenceMatcher(None, a, b).ratio(), cobertura)


_SI = {
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


def _respuesta_si_no(texto: str) -> bool | None:
    t = _normalizar_basico(texto)
    # AssemblyAI transcribe un "sí" corto como "S." y un "no" como "N." (medido 2026-08-28)
    if t in {"s", "si si", "s s"}:
        return True
    if t in {"n", "no no"}:
        return False
    palabras = set(t.split())
    si = {_normalizar_basico(s) for s in _SI}
    no = {_normalizar_basico(s) for s in _NO}
    # Frases de varias palabras ("eso es", "así es") se buscan como subcadena; el STT puede
    # anteponer ruido ("Ese, eso es.")
    hay_si = bool(palabras & si) or any(" " in s and s in t for s in si)
    hay_no = bool(palabras & no) or any(" " in s and s in t for s in no)
    if hay_si and not hay_no:
        return True
    if hay_no or t.startswith("no"):
        return False
    return None


_PISOS = {
    "bajo": "0", "primero": "1", "primer": "1", "primera": "1", "segundo": "2", "segunda": "2",
    "tercero": "3", "tercer": "3", "tercera": "3", "cuarto": "4", "cuarta": "4", "quinto": "5",
    "quinta": "5", "sexto": "6", "sexta": "6", "septimo": "7", "septima": "7", "octavo": "8",
    "octava": "8", "noveno": "9", "novena": "9", "decimo": "10", "decima": "10", "atico": "atico",
    "entresuelo": "entresuelo", "principal": "principal",
}  # fmt: skip


def _normalizar_portal(valor: str | None) -> str | None:
    if valor is None:
        return None
    v = _numeros_a_cifras(_normalizar_basico(str(valor)))
    v = re.sub(r"\bportal\b", "", v).strip()
    # Un portal es un número (1-2 cifras) o una sola letra; "el", "ese", "sin"... son ruido
    if not v or not re.fullmatch(r"\d{1,2}|[a-z]", v):
        return None
    return v


def _normalizar_piso(valor: str | None) -> str | None:
    if valor is None:
        return None
    v = _normalizar_basico(str(valor))
    v = re.sub(r"\b(piso|planta)\b", "", v).strip()
    v = _PISOS.get(v, v)
    v = _numeros_a_cifras(v)
    m = re.match(r"(\d+)", v)
    return m.group(1) if m else (v or None)


def _normalizar_puerta(valor: str | None) -> str | None:
    if valor is None:
        return None
    v = _normalizar_basico(str(valor)).replace("puerta", "").replace("letra", "").strip()
    v = {"izquierda": "izq", "derecha": "dcha"}.get(v, v)
    return v.upper() or None


_ORDINALES = "|".join(k for k in _PISOS if k not in {"bajo", "atico", "entresuelo", "principal"})


def _extraer_vivienda_regex(texto: str) -> tuple[str | None, str | None, str | None]:
    """Respaldo determinista para formatos habituales: 'portal 2', '3º B', 'tercero B'."""
    t = _normalizar_basico(texto)
    m_portal = re.search(r"\bportal\s+(\w+)", t)
    portal = _normalizar_portal(m_portal.group(1)) if m_portal else None
    resto = re.sub(r"\bportal\s+\w+", " ", t)
    m_piso = re.search(rf"\b(\d{{1,2}}|{_ORDINALES}|bajo|atico)\b\s*(?:piso|planta)?", resto)
    piso = _normalizar_piso(m_piso.group(1)) if m_piso else None
    puerta = None
    m_explicita = re.search(r"\b(?:puerta|letra)\s+([a-z]|\d{1,2}|izquierda|derecha)\b", resto)
    if m_explicita:
        puerta = _normalizar_puerta(m_explicita.group(1))
    else:
        m_puerta = re.search(r"\b([a-z]|\d{1,2}|izquierda|derecha)\b\s*$", resto.strip())
        if m_puerta and (m_piso is None or m_puerta.start() > m_piso.start()):
            puerta = _normalizar_puerta(m_puerta.group(1))
    return portal, piso, puerta


def _candidatas_vivienda(
    viviendas: list[Vivienda], portal: str | None, piso: str | None, puerta: str | None
) -> list[Vivienda]:
    """Viviendas compatibles con lo dicho. Varias => falta el portal; ninguna => no existe."""
    candidatas = [
        v
        for v in viviendas
        if _normalizar_piso(v.piso) == piso and _normalizar_puerta(v.puerta) == puerta
    ]
    if portal is not None:
        candidatas = [v for v in candidatas if _normalizar_portal(v.portal) == portal]
    return candidatas


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


def _es_correccion_real(nuevo: str, anterior: str, entrada: str) -> bool:
    """Una corrección del problema debe aportar algo nuevo y hablar del problema, no del bot.

    'Dirás que has podido clasificarlo con urgencia baja' es un comentario sobre el asistente:
    el modelo lo devolvió como `problema` y el grafo lo tomó como corrección (2026-08-28)."""
    e = _normalizar_basico(entrada)
    if any(m in e for m in _META_BOT):
        return False
    a, b = set(_normalizar_basico(nuevo).split()), set(_normalizar_basico(anterior).split())
    if not a:
        return False
    return len(a - b) / len(a) >= 0.4  # al menos un 40% de palabras nuevas


_NO_OIDO = (
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


def _no_lo_oyo(texto: str, estado: EstadoLlamada) -> bool:
    """Pide repetir, o saluda/pregunta si hay alguien tras un mensaje que no llegó a sonar entero."""
    t = _normalizar_basico(texto)
    if any(ch.isdigit() for ch in t) or len(t.split()) > 7:
        return False
    if any(f in t for f in _NO_OIDO):
        return True
    entrega = estado.get("entrega_ultima") or {}
    no_entregado = entrega.get("estado") in ("interrumpida", "no_reproducida") or (
        entrega.get("ratio") is not None and entrega.get("ratio", 1.0) < 0.8
    )
    return bool(
        no_entregado and (t in {"hola", "oiga", "oye", "diga", "hola hola"} or t.startswith("hola"))
    )


_RELLENO = (
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


def _es_relleno(texto: str) -> bool:
    """'Ah, vale, espéreme': el vecino pide tiempo. Ni dato ni fallo: se espera."""
    t = _normalizar_basico(texto)
    if any(ch.isdigit() for ch in t) or len(t.split()) > 6:
        return False
    return any(r in t for r in _RELLENO)


_SALUDOS = (
    "hola",
    "buenas",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "que tal",
    "oiga",
    "digame",
)


def _es_saludo(texto: str) -> bool:
    """Solo saludo/muletilla, sin datos: no debe contar como intento fallido."""
    t = _normalizar_basico(texto)
    if any(ch.isdigit() for ch in t) or len(t.split()) > 5:
        return False
    return any(s in t for s in _SALUDOS)


# --------------------------------------------------------------------------- utilidades de estado


#: Transiciones observadas que el manifiesto no declara. Se exponen en `/api/metricas` para
#: que una arista sin declarar se vea, en vez de romper la llamada de un vecino.
VIOLACIONES_MANIFIESTO: list[str] = []


def _verificar(estado: EstadoLlamada, destino: str) -> None:
    """Comprueba la transición contra el manifiesto. Bajo pytest lanza; en producción, anota."""
    motivo = verificar_transicion(estado.get("nodo_actual"), destino)
    if motivo is not None:
        logger.warning("Transición no declarada en el manifiesto: %s", motivo)
        if motivo not in VIOLACIONES_MANIFIESTO:
            VIOLACIONES_MANIFIESTO.append(motivo)


def _con_traza(estado: EstadoLlamada, nodo: str, accion: str, detalle: str = "") -> list[Traza]:
    return [*estado.get("traza", []), Traza(nodo=nodo, accion=accion, detalle=detalle)]


def _anotar_plantilla(estado: EstadoLlamada, nodo: str, texto: Any, extra: dict[str, Any]) -> None:
    """Deja en la traza de qué plantilla y versión salió el mensaje.

    En cuanto los textos son editables, una auditoría que solo guarde la frase no permite saber
    qué se dijo ni con qué versión. `render` devuelve un `str` que lleva su propia identidad, así
    que anotarlo no cuesta cablear el id por cada firma.
    """
    plantilla_id = getattr(texto, "plantilla_id", None)
    if plantilla_id is None:
        return
    base = extra.get("traza", estado.get("traza", []))
    extra["traza"] = [
        *base,
        Traza(nodo=nodo, accion="plantilla", detalle=f"{plantilla_id} v{texto.version}"),
    ]


def _preguntar(estado: EstadoLlamada, nodo: str, texto: str, **extra: Any) -> dict[str, Any]:
    # `historial` en extra ya incluye el turno del vecino; el mensaje del bot se añade después.
    _verificar(estado, nodo)
    _anotar_plantilla(estado, nodo, texto, extra)
    base = extra.pop("historial", estado.get("historial", []))
    return {
        "nodo_actual": nodo,
        "esperando_usuario": True,
        "entrada_usuario": None,
        **extra,
        "mensajes_bot": [*estado.get("mensajes_bot", []), texto],
        "historial": [*base, {"rol": "bot", "texto": texto}],
        # Para repetir/retomar se guarda solo la pregunta, sin saludos ni preámbulos
        "ultima_pregunta": texto.split(". ", 1)[1]
        if texto.startswith("Hola") and ". " in texto
        else texto,
    }


def _avanzar(
    estado: EstadoLlamada, nodo: str, mensaje: str | None = None, **extra: Any
) -> dict[str, Any]:
    _verificar(estado, nodo)
    _anotar_plantilla(estado, nodo, mensaje, extra)
    base = extra.pop("historial", estado.get("historial", []))
    cambios: dict[str, Any] = {
        "nodo_actual": nodo,
        "esperando_usuario": False,
        "entrada_usuario": None,
        "historial": base,
        **extra,
    }
    if mensaje:
        cambios["mensajes_bot"] = [*estado.get("mensajes_bot", []), mensaje]
        cambios["historial"] = [*base, {"rol": "bot", "texto": mensaje}]
    return cambios


def _registrar_entrada(estado: EstadoLlamada) -> list[dict[str, str]]:
    entrada = estado.get("entrada_usuario")
    if entrada is None:
        return estado.get("historial", [])
    return [*estado.get("historial", []), {"rol": "vecino", "texto": entrada}]


def _config(estado: EstadoLlamada) -> ConfiguracionEfectiva:
    doc = estado.get("configuracion")
    return ConfiguracionEfectiva.model_validate(doc) if doc else DEFAULTS


def _catalogo(estado: EstadoLlamada) -> list[str]:
    return [*_config(estado).catalogo_categorias, CATEGORIA_NO_CLASIFICABLE]


def _tiempos(estado: EstadoLlamada, **valores: Any) -> dict[str, Any]:
    t = dict(estado.get("tiempos_turno", {}))
    for k, v in valores.items():
        if k == "llm_llamadas":
            t[k] = t.get(k, 0) + v
        else:
            t[k] = v
    return t


def _llamada_desde(estado: EstadoLlamada, resultado: str, motivo: str | None = None) -> Llamada:
    return Llamada(
        id=estado["llamada_id"],
        iniciada_en=datetime.fromisoformat(estado["iniciada_en"]),
        telefono_llamante=estado.get("telefono_llamante"),
        finca_id=estado.get("finca_id"),
        vivienda_id=estado.get("vivienda_id"),
        nombre_declarado=estado.get("nombre_declarado"),
        transcripcion=estado.get("historial", []),
        resumen=estado.get("resumen") or estado.get("solicitud"),
        resultado=resultado,  # type: ignore[arg-type]
        motivo_revision=motivo,
        configuracion_id=estado.get("configuracion_id"),
        grafo=estado.get("grafo"),
    )


# --------------------------------------------------------------------------- interpretación


def _fusionar_pendientes(estado: EstadoLlamada, interp: Interpretacion) -> dict[str, str]:
    pend = dict(estado.get("pendientes", {}))
    for campo in ("direccion", "portal", "piso", "puerta", "nombre", "problema"):
        valor = getattr(interp, campo)
        if valor:
            pend[campo] = valor
    if interp.problema:
        pend["problema_corto"] = interp.problema
        for campo in ("categoria", "urgencia"):
            valor = getattr(interp, campo)
            if valor:
                pend[campo] = valor
    return pend


_PALABRAS_PERSONA = (
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
_PALABRAS_DESPEDIDA = (
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


def _pide_persona(texto: str) -> bool:
    t = _normalizar_basico(texto)
    return any(p in t for p in _PALABRAS_PERSONA)


def _se_despide(texto: str) -> bool:
    t = _normalizar_basico(texto)
    return any(p in t for p in _PALABRAS_DESPEDIDA)


_PREGUNTAS_DETALLE: dict[str, str] = {
    "electricidad": "Para orientar al electricista: ¿es una sola luz o varias, y no encienden o parpadean?",
    "fontaneria": "Para orientar al fontanero: ¿gotea o sale agua con fuerza, y sabe de dónde viene?",
    "ascensor": "¿El ascensor está parado del todo o funciona a ratos? ¿Hay alguien dentro?",
    "cerrajeria": "¿La puerta no cierra, no abre, o es la cerradura? ¿Desde cuándo?",
    "limpieza": "¿En qué zona exactamente y desde cuándo?",
    "albanileria": "¿Dónde exactamente está el daño y qué tamaño tiene, aproximadamente?",
    "pintura": "¿Qué zona hay que pintar exactamente y qué le pasa: humedad, desconchones, pintadas?",
    "_": "¿Puede darme algún detalle más: dónde exactamente y desde cuándo ocurre?",
}


_FAQ: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("grab", "registr"),
        "Sí: la llamada se registra para gestionar su incidencia y para control de calidad. ",
    ),
    (
        ("tard", "cuando", "plazo", "vend"),
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


def _responder_pregunta(texto: str) -> str:
    t = _normalizar_basico(texto)
    for claves, respuesta in _FAQ:
        if any(c in t for c in claves):
            return respuesta
    return "Solo puedo gestionar incidencias y solicitudes de su finca. "


def _es_pregunta_faq(texto: str) -> bool:
    """Pregunta al asistente reconocible sin LLM: interrogación + palabra clave y sin datos."""
    n = _normalizar_basico(texto)
    # El STT no siempre pone signos de interrogación: basta la palabra clave y que no haya datos
    if any(ch.isdigit() for ch in n) or len(n.split()) > 8:
        return False
    return any(c in n for claves, _ in _FAQ for c in claves)


def construir_nodos(deps: Dependencias) -> dict[str, Any]:
    """Devuelve {nombre_nodo: función async} para registrarlos en un grafo."""

    async def _interpretar(
        estado: EstadoLlamada, entrada: str, nodo: str, *, si_no: bool = False
    ) -> tuple[Interpretacion, dict[str, Any]]:
        """Determinista cuando basta; si no, una llamada al LLM. Devuelve (interp, tiempos)."""
        if si_no:
            r = _respuesta_si_no(entrada)
            if r is not None and len(entrada.split()) <= 4:
                interp = Interpretacion(confirma=r, origen="determinista")
                return interp, _tiempos(estado, origen_interpretacion="determinista")
        if _es_pregunta_faq(entrada):
            interp = Interpretacion(intencion="pregunta", pregunta=entrada, origen="determinista")
            return interp, _tiempos(estado, origen_interpretacion="determinista")
        # ¿Ya se lanzó esta interpretación sobre el transcript parcial? Se reutiliza (o se espera
        # a que termine): solapa el LLM con la detección de fin de turno del STT.
        clave = clave_especulacion(entrada)
        propias = deps.especulaciones.pop(estado["llamada_id"], {})
        futuro = propias.pop(clave, None)
        if futuro is None and propias:
            # El parcial y el final suelen diferir en una palabra o en un número en letra:
            # se acepta la especulación más parecida si supera el 90 % de similitud
            mejor, puntuacion = max(
                (
                    (k, SequenceMatcher(None, _normalizar(k), _normalizar(clave)).ratio())
                    for k in propias
                ),
                key=lambda p: p[1],
            )
            # Con menos parecido solo vale si los datos "duros" (números y letras sueltas:
            # pisos, puertas, portales) son idénticos en el parcial y en el final
            duros = re.compile(r"\b(?:\d+|[a-z])\b")
            mismos_duros = duros.findall(_normalizar(mejor)) == duros.findall(_normalizar(clave))
            if puntuacion >= 0.9 or (puntuacion >= 0.8 and mismos_duros):
                futuro = propias.pop(mejor)
        for otro in propias.values():
            otro.cancel()
        if futuro is not None and not futuro.cancelled():
            t0 = perf_counter()
            try:
                interp = await futuro
                return interp, _tiempos(
                    estado,
                    interpretacion_ms=int((perf_counter() - t0) * 1000),
                    llm_llamadas=1,
                    origen_interpretacion="llm_especulativo",
                )
            except Exception:  # noqa: BLE001 - la especulación falló: interpretación normal
                logger.warning("Especulación fallida en %s; se interpreta de nuevo", nodo)
        interp = await interpretar(
            deps.llm, estado.get("ultima_pregunta"), entrada, nodo, catalogo=_catalogo(estado)
        )
        return interp, _tiempos(
            estado, interpretacion_ms=interp.llm_ms, llm_llamadas=1, origen_interpretacion="llm"
        )

    def _vista_interp(interp: Interpretacion) -> dict[str, Any]:
        return {
            **interp.model_dump(exclude_none=True),
            "origen": interp.origen,
            "llm_ms": interp.llm_ms,
        }

    def _intencion_global(
        estado: EstadoLlamada,
        nodo: str,
        interp: Interpretacion,
        historial: list[dict[str, str]],
        extra: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Atiende repetir/pregunta/fuera_ambito/humano/colgar. None si el turno sigue su curso."""
        ultima = estado.get("ultima_pregunta") or "¿En qué puedo ayudarle?"
        aporta_datos = bool(interp.campos_presentes()) or interp.confirma is not None
        if interp.intencion == "repetir":
            # Se repite el mensaje completo (resumen incluido), no solo la pregunta final
            completo = estado.get("ultimo_mensaje_bot") or ultima
            return _preguntar(
                estado,
                nodo,
                render("global.repetir", mensaje=completo),
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "repetir"),
                **extra,
            )
        if (interp.intencion == "pregunta" or interp.pregunta) and not aporta_datos:
            return _preguntar(
                estado,
                nodo,
                _responder_pregunta(interp.pregunta or estado.get("entrada_usuario") or "")
                + ultima,
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "pregunta"),
                **extra,
            )
        if interp.intencion == "fuera_ambito" and not aporta_datos:
            return _preguntar(
                estado,
                nodo,
                render("global.fuera_ambito", pregunta=ultima),
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "fuera_ambito"),
                **extra,
            )
        # Derivar o colgar son irreversibles: exigen palabras explícitas del vecino, no solo la
        # opinión del modelo (una dirección mal transcrita llegó a derivarse como "humano").
        entrada = estado.get("entrada_usuario") or ""
        if interp.intencion == "humano" and _pide_persona(entrada):
            return _avanzar(
                estado,
                "derivar_humano",
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "humano"),
                **extra,
            )
        if interp.intencion == "colgar" and not aporta_datos and _se_despide(entrada):
            return _avanzar(
                estado,
                "despedida",
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "colgar"),
                **extra,
            )
        return None

    async def _turno(
        estado: EstadoLlamada, nodo: str, *, si_no: bool = False
    ) -> tuple[Interpretacion, list[dict[str, str]], dict[str, Any], dict[str, Any] | None]:
        """Registra la entrada, interpreta y atiende intenciones globales.

        Devuelve (interp, historial, extra_estado, salida_global_o_None)."""
        entrada = estado["entrada_usuario"] or ""
        historial = _registrar_entrada(estado)
        if _no_lo_oyo(entrada, estado):
            completo = estado.get("ultimo_mensaje_bot") or estado.get("ultima_pregunta") or ""
            salida_rep = _preguntar(
                estado,
                nodo,
                render("global.no_oido", mensaje=completo),
                historial=historial,
                tiempos_turno=_tiempos(estado, origen_interpretacion="determinista"),
                interpretacion={"intencion": "repetir", "origen": "determinista", "llm_ms": 0},
                traza=_con_traza(estado, nodo, "repetir_no_oido", entrada),
            )
            salida_rep["ultima_pregunta"] = estado.get("ultima_pregunta")
            return (
                Interpretacion(intencion="repetir", origen="determinista"),
                historial,
                {},
                salida_rep,
            )
        if _es_relleno(entrada):
            salida_relleno = _preguntar(
                estado,
                nodo,
                render("global.relleno"),
                historial=historial,
                tiempos_turno=_tiempos(estado, origen_interpretacion="determinista"),
                interpretacion={"intencion": "relleno", "origen": "determinista", "llm_ms": 0},
                traza=_con_traza(estado, nodo, "relleno", entrada),
            )
            salida_relleno["ultima_pregunta"] = estado.get("ultima_pregunta")
            return Interpretacion(origen="determinista"), historial, {}, salida_relleno
        interp, tiempos = await _interpretar(estado, entrada, nodo, si_no=si_no)
        extra = {"tiempos_turno": tiempos, "interpretacion": _vista_interp(interp)}
        salida = _intencion_global(estado, nodo, interp, historial, extra)
        return interp, historial, extra, salida

    # ------------------------------------------------------------------ enrutador determinista

    async def siguiente_paso(estado: EstadoLlamada) -> dict[str, Any]:
        """Decide el siguiente nodo según lo que falta, consumiendo lo que el vecino ya dijo."""
        nodo = "siguiente_paso"
        pend = dict(estado.get("pendientes", {}))
        cambios: dict[str, Any] = {}
        traza = list(estado.get("traza", []))
        validaciones = list(estado.get("validaciones", []))

        if not estado.get("finca_id"):
            direccion = pend.pop("direccion", None)
            if direccion:
                fincas = await deps.repo.listar_fincas()
                puntuadas = sorted(
                    ((_similitud_direccion(direccion, f.direccion), f) for f in fincas),
                    key=lambda p: p[0],
                    reverse=True,
                )
                if puntuadas and puntuadas[0][0] >= UMBRAL_FINCA:
                    puntuacion, finca = puntuadas[0]
                    traza.append(
                        Traza(
                            nodo=nodo,
                            accion="candidata",
                            detalle=f"{finca.direccion} ({puntuacion:.2f})",
                        )
                    )
                    return _preguntar(
                        estado,
                        "identificar_finca",
                        render("identificar_finca.candidata", direccion=finca.direccion),
                        finca_candidata_id=finca.id,
                        pendientes=pend,
                        traza=traza,
                    )
                return _reintento_finca(
                    estado,
                    render("identificar_finca.motivo_no_encontrada"),
                    pend,
                    traza,
                )
            return _avanzar(estado, "identificar_finca", pendientes=pend, traza=traza)

        if not estado.get("vivienda_id"):
            piso, puerta = pend.get("piso"), pend.get("puerta")
            if piso and puerta:
                portal = _normalizar_portal(pend.get("portal"))
                np_, npu = _normalizar_piso(piso), _normalizar_puerta(puerta)
                viviendas = await deps.repo.viviendas_de(estado["finca_id"])  # type: ignore[arg-type]
                if portal and not any(v.portal for v in viviendas):
                    portal = None  # la finca no tiene portales: el "portal" es ruido
                candidatas = _candidatas_vivienda(viviendas, portal, np_, npu)
                traza.append(
                    Traza(
                        nodo=nodo,
                        accion="vivienda",
                        detalle=f"portal={portal} piso={np_} puerta={npu} candidatas={len(candidatas)}",
                    )
                )
                if len(candidatas) == 1:
                    vivienda = candidatas[0]
                    for k in ("portal", "piso", "puerta"):
                        pend.pop(k, None)
                    cambios.update(vivienda_id=vivienda.id, vivienda_etiqueta=vivienda.etiqueta())
                    validaciones.append("vivienda_existe_en_finca")
                    estado = {**estado, **cambios}  # type: ignore[assignment]
                elif len(candidatas) > 1:
                    # Mismo piso y puerta en varios portales: solo falta el portal (no es un fallo)
                    pend.pop("portal", None)
                    portales = sorted({v.portal or "" for v in candidatas})
                    return _preguntar(
                        estado,
                        "identificar_vivienda",
                        render(
                            "identificar_vivienda.varios_portales",
                            piso=np_,
                            puerta=npu,
                            portales=" o ".join(portales),
                        ),
                        pendientes=pend,
                        traza=traza,
                        **cambios,
                    )
                else:
                    for k in ("portal", "piso", "puerta"):
                        pend.pop(k, None)
                    return await _reintento_vivienda(estado, pend, traza)
            elif piso or puerta or pend.get("portal"):
                # Dato parcial ("el tercero", "portal 1"): se conserva y se pide solo lo que falta.
                # Si el vecino repite lo mismo sin avanzar, sí consume intento (evita bucles).
                previo = estado.get("pendientes", {})
                sin_avance = (
                    all(previo.get(k) == pend.get(k) for k in ("portal", "piso", "puerta"))
                    and estado.get("entrada_usuario") is not None
                )
                if sin_avance:
                    return await _reintento_vivienda(estado, pend, traza)
                falta = "la puerta" if piso else "el piso" if puerta else "el piso y la puerta"
                tiene = ", ".join(
                    f"{k} {v}"
                    for k, v in (("portal", pend.get("portal")), ("piso", piso), ("puerta", puerta))
                    if v
                )
                return _preguntar(
                    estado,
                    "identificar_vivienda",
                    render("identificar_vivienda.parcial", tiene=tiene, falta=falta),
                    pendientes=pend,
                    traza=[*traza, Traza(nodo=nodo, accion="vivienda_parcial", detalle=tiene)],
                    **cambios,
                )
            else:
                return _avanzar(
                    estado, "identificar_vivienda", pendientes=pend, traza=traza, **cambios
                )

        # El nombre no se pregunta: con la finca y la vivienda basta. Si el vecino lo dice por
        # su cuenta, se guarda para el resumen y la ficha.
        if not estado.get("nombre_declarado") and (nombre := pend.pop("nombre", None)):
            cambios["nombre_declarado"] = nombre
            traza.append(Traza(nodo=nodo, accion="nombre_registrado", detalle=nombre))
            estado = {**estado, **cambios}  # type: ignore[assignment]

        if not estado.get("solicitud"):
            problema = pend.pop("problema", None)
            if problema:
                cambios["solicitud"] = problema
                for k in ("categoria", "urgencia", "resumen"):
                    v = pend.pop(k, None)
                    if v:
                        cambios["urgencia_declarada" if k == "urgencia" else k] = v
                if cambios.get("categoria"):
                    # El resumen corto lo da la interpretación; si falta, vale la propia solicitud
                    cambios.setdefault("resumen", pend.pop("problema_corto", None) or problema)
                    cambios["senales_confianza"] = {
                        "modelo": deps.llm.nombre,
                        "version_prompt": VERSION_INTERPRETACION,
                        "categoria_en_catalogo": cambios["categoria"] in _catalogo(estado),
                        "fusionada_con_interpretacion": True,
                    }
                traza.append(Traza(nodo=nodo, accion="solicitud_recogida", detalle=problema[:60]))
                estado = {**estado, **cambios}  # type: ignore[assignment]
            else:
                return _avanzar(
                    estado,
                    "recoger_solicitud",
                    pendientes=pend,
                    traza=traza,
                    validaciones=validaciones,
                    **cambios,
                )

        if not estado.get("categoria"):
            destino = "clasificar_problema"
        elif not estado.get("urgencia"):
            destino = "determinar_urgencia"
        elif (
            not estado.get("detalle_pedido")
            and _config(estado).pedir_detalle
            and estado.get("confirmado") is None
            and _merece_detalle(estado)
        ):
            destino = "pedir_detalle"
        elif estado.get("confirmado") is not True:
            destino = "confirmar_resumen"
        else:
            destino = "validar_datos"
        return _avanzar(
            estado, destino, pendientes=pend, traza=traza, validaciones=validaciones, **cambios
        )

    def _reintento_finca(
        estado: EstadoLlamada,
        prefijo: str,
        pend: dict[str, str],
        traza: list[Traza],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        intentos = estado.get("intentos_finca", 0) + 1
        if intentos >= DEFAULTS.intentos_finca:
            return _avanzar(
                estado,
                "finca_no_identificada",
                intentos_finca=intentos,
                finca_candidata_id=None,
                pendientes=pend,
                traza=[
                    *traza,
                    Traza(
                        nodo="identificar_finca", accion="agotados_intentos", detalle=str(intentos)
                    ),
                ],
                **(extra or {}),
            )
        return _preguntar(
            estado,
            "identificar_finca",
            render("identificar_finca.reintento", motivo=prefijo),
            intentos_finca=intentos,
            finca_candidata_id=None,
            pendientes=pend,
            traza=[
                *traza,
                Traza(nodo="identificar_finca", accion="reintento", detalle=str(intentos)),
            ],
            **(extra or {}),
        )

    async def _describir_viviendas(finca_id: str) -> str:
        """'pisos del 1 al 4 y puertas A o B' a partir de las viviendas reales de la finca."""
        viviendas = await deps.repo.viviendas_de(finca_id)
        if not viviendas:
            return ""
        pisos = sorted(
            {_normalizar_piso(v.piso) or v.piso for v in viviendas},
            key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x),
        )
        puertas = sorted({v.puerta for v in viviendas})
        portales = sorted({v.portal for v in viviendas if v.portal})
        partes = []
        if portales:
            partes.append("portales " + " y ".join(portales))
        partes.append(
            f"pisos del {pisos[0]} al {pisos[-1]}" if len(pisos) > 1 else f"piso {pisos[0]}"
        )
        partes.append(
            "puertas " + " o ".join(puertas) if len(puertas) > 1 else f"puerta {puertas[0]}"
        )
        return "En esa finca hay " + ", ".join(partes) + ". "

    async def _reintento_vivienda(
        estado: EstadoLlamada,
        pend: dict[str, str],
        traza: list[Traza],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        intentos = estado.get("intentos_vivienda", 0) + 1
        if intentos >= _config(estado).intentos_vivienda:
            return _avanzar(
                estado,
                "vivienda_no_identificada",
                intentos_vivienda=intentos,
                pendientes=pend,
                traza=traza,
                **(extra or {}),
            )
        ayuda = await _describir_viviendas(estado["finca_id"]) if estado.get("finca_id") else ""
        return _preguntar(
            estado,
            "identificar_vivienda",
            render("identificar_vivienda.reintento", ayuda=ayuda),
            intentos_vivienda=intentos,
            pendientes=pend,
            traza=traza,
            **(extra or {}),
        )

    async def _coincide_finca(direccion: str) -> bool:
        fincas = await deps.repo.listar_fincas()
        return any(_similitud_direccion(direccion, f.direccion) >= UMBRAL_FINCA for f in fincas)

    # ------------------------------------------------------------------ nodos que preguntan

    async def identificar_finca(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "identificar_finca"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                render("identificar_finca.saludo"),
            )
        candidata = estado.get("finca_candidata_id")
        if candidata:
            interp, historial, extra, salida = await _turno(estado, nodo, si_no=True)
            if salida:
                return salida
            if interp.confirma is True and not interp.direccion:
                finca = await deps.repo.obtener_finca(candidata)
                assert finca is not None
                cfg, cfg_id = await deps.repo.configuracion_efectiva(finca.id)
                return _avanzar(
                    estado,
                    "siguiente_paso",
                    historial=historial,
                    finca_id=finca.id,
                    finca_direccion=finca.direccion,
                    finca_candidata_id=None,
                    configuracion=cfg.model_dump(),
                    configuracion_id=cfg_id,
                    pendientes=_fusionar_pendientes(estado, interp),
                    validaciones=[*estado.get("validaciones", []), "finca_confirmada_verbalmente"],
                    traza=_con_traza(estado, nodo, "finca_confirmada", finca.direccion),
                    **extra,
                )
            if interp.direccion:
                # Rechaza la candidata aportando otra dirección: se evalúa sin gastar intento extra
                return _avanzar(
                    estado,
                    "siguiente_paso",
                    historial=historial,
                    finca_candidata_id=None,
                    pendientes=_fusionar_pendientes(estado, interp),
                    traza=_con_traza(estado, nodo, "candidata_rechazada", "nueva dirección"),
                    **extra,
                )
            if interp.confirma is False:
                return _reintento_finca(
                    {**estado, "historial": historial},
                    render("identificar_finca.motivo_no_es_esa"),
                    _fusionar_pendientes(estado, interp),
                    estado.get("traza", []),
                    extra,
                )
            return _preguntar(
                estado,
                nodo,
                render("identificar_finca.confirmar_si_no"),
                historial=historial,
                **extra,
            )

        # Sin candidata: primero coincidencia determinista de la frase completa (0 llamadas LLM)
        historial = _registrar_entrada(estado)
        if _es_saludo(entrada):
            # "Hola, buenas": se devuelve el saludo y se repite la pregunta sin gastar intento
            return _preguntar(
                estado,
                nodo,
                render("identificar_finca.saludo_respuesta"),
                historial=historial,
                tiempos_turno=_tiempos(estado, origen_interpretacion="determinista"),
                interpretacion={"intencion": "saludo", "origen": "determinista", "llm_ms": 0},
                traza=_con_traza(estado, nodo, "saludo", entrada),
            )
        fincas = await deps.repo.listar_fincas()
        puntuadas = sorted(
            ((_similitud_direccion(entrada, f.direccion), f) for f in fincas),
            key=lambda p: p[0],
            reverse=True,
        )
        if puntuadas and puntuadas[0][0] >= UMBRAL_FINCA and len(entrada.split()) <= 8:
            puntuacion, finca = puntuadas[0]
            return _preguntar(
                estado,
                nodo,
                render("identificar_finca.candidata", direccion=finca.direccion),
                historial=historial,
                finca_candidata_id=finca.id,
                tiempos_turno=_tiempos(estado, origen_interpretacion="determinista"),
                interpretacion={"direccion": entrada, "origen": "determinista", "llm_ms": 0},
                traza=_con_traza(
                    estado, nodo, "candidata", f"{finca.direccion} ({puntuacion:.2f})"
                ),
            )
        interp, historial, extra, salida = await _turno(estado, nodo)
        if salida:
            return salida
        pend = _fusionar_pendientes(estado, interp)
        if not interp.direccion:
            return _reintento_finca(
                {**estado, "historial": historial},
                render("identificar_finca.motivo_no_entendida"),
                pend,
                estado.get("traza", []),
                extra,
            )
        return _avanzar(estado, "siguiente_paso", historial=historial, pendientes=pend, **extra)

    async def finca_no_identificada(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "finca_no_identificada"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                render("finca_no_identificada.recoger"),
            )
        historial = _registrar_entrada(estado)
        llamada = _llamada_desde(
            {**estado, "historial": historial, "solicitud": entrada}, "finca_no_identificada"
        )
        await deps.repo.guardar_llamada(llamada)
        return _avanzar(
            estado,
            "fin",
            render("finca_no_identificada.despedida"),
            historial=historial,
            solicitud=entrada,
            resultado="finca_no_identificada",
            finalizada=True,
            traza=_con_traza(estado, nodo, "llamada_registrada", "sin incidencia técnica"),
        )

    async def identificar_vivienda(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "identificar_vivienda"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                render("identificar_vivienda.preguntar"),
            )
        # Un "sí"/"no" suelto con datos parciales pendientes: no hay nada que extraer, se repite
        # la pregunta pendiente (el STT transcribe "sí" como "S.")
        pend_previo = estado.get("pendientes", {})
        if (
            _respuesta_si_no(entrada) is not None
            and len(entrada.split()) <= 3
            and (pend_previo.get("piso") or pend_previo.get("puerta") or pend_previo.get("portal"))
        ):
            return _preguntar(
                estado,
                nodo,
                estado.get("ultima_pregunta") or render("identificar_vivienda.repregunta"),
                historial=_registrar_entrada(estado),
                tiempos_turno=_tiempos(estado, origen_interpretacion="determinista"),
                interpretacion={"confirma": True, "origen": "determinista", "llm_ms": 0},
            )
        # Formato corto y reconocible: sin LLM (bóveda: regex solo para formatos)
        portal, piso, puerta = _extraer_vivienda_regex(entrada)
        if piso and puerta and len(entrada.split()) <= 5:
            pend = {**estado.get("pendientes", {}), "piso": piso, "puerta": puerta}
            if portal:
                pend["portal"] = portal
            return _avanzar(
                estado,
                "siguiente_paso",
                historial=_registrar_entrada(estado),
                pendientes=pend,
                tiempos_turno=_tiempos(estado, origen_interpretacion="determinista"),
                interpretacion={
                    "portal": portal,
                    "piso": piso,
                    "puerta": puerta,
                    "origen": "determinista",
                    "llm_ms": 0,
                },
                traza=_con_traza(
                    estado, nodo, "extraccion_regex", f"portal={portal} piso={piso} puerta={puerta}"
                ),
            )
        interp, historial, extra, salida = await _turno(estado, nodo)
        if salida:
            return salida
        pend = _fusionar_pendientes(estado, interp)
        # El LLM es la primera fuente de comprensión; el regex solo entra si lo que entendió el
        # modelo no corresponde a ninguna vivienda de la finca (p. ej. inventó portal="a").
        if piso and puerta and estado.get("finca_id"):
            viviendas = await deps.repo.viviendas_de(estado["finca_id"])  # type: ignore[arg-type]
            portal_llm = _normalizar_portal(pend.get("portal"))
            if portal_llm and not any(v.portal for v in viviendas):
                portal_llm = None
            del_llm = _candidatas_vivienda(
                viviendas,
                portal_llm,
                _normalizar_piso(pend.get("piso")),
                _normalizar_puerta(pend.get("puerta")),
            )
            if not del_llm and _candidatas_vivienda(viviendas, portal, piso, puerta):
                pend["piso"], pend["puerta"] = piso, puerta
                if portal:
                    pend["portal"] = portal
                else:
                    pend.pop("portal", None)
            elif not del_llm:
                # Lo del modelo no existe en la finca; nos quedamos con lo que el regex sí vio
                for k, v in (("portal", portal), ("piso", piso), ("puerta", puerta)):
                    if v:
                        pend[k] = v
                    else:
                        pend.pop(k, None)
        # El regex completa los campos de formato que el modelo no dio ("puerta A" tras "el tercero")
        for k, v in (("portal", portal), ("piso", piso), ("puerta", puerta)):
            if v and not pend.get(k):
                pend[k] = v
        if not (pend.get("piso") and pend.get("puerta")):
            # Sin piso ni puerta ni portal: no ha dado ningún dato de vivienda -> reintento.
            # Con algún dato parcial, `siguiente_paso` lo conserva y pide solo lo que falta.
            if not (pend.get("piso") or pend.get("puerta") or pend.get("portal")):
                return await _reintento_vivienda(
                    {**estado, "historial": historial},
                    pend,
                    estado.get("traza", []),
                    extra,
                )
        return _avanzar(estado, "siguiente_paso", historial=historial, pendientes=pend, **extra)

    async def vivienda_no_identificada(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "vivienda_no_identificada"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                render("vivienda_no_identificada.recoger"),
            )
        historial = _registrar_entrada(estado)
        cfg = _config(estado)
        destino = cfg.contacto.correo if cfg.contacto else "administrador"
        llamada = _llamada_desde(
            {**estado, "historial": historial, "solicitud": entrada}, "vivienda_no_identificada"
        )
        await deps.repo.guardar_llamada(llamada)
        return _avanzar(
            estado,
            "fin",
            render("vivienda_no_identificada.despedida"),
            historial=historial,
            solicitud=entrada,
            resultado="vivienda_no_identificada",
            finalizada=True,
            canal_aviso="correo",
            destino_aviso=destino,
            traza=_con_traza(
                estado, nodo, "correo_simulado", f"resumen al administrador -> {destino}"
            ),
        )

    async def recoger_solicitud(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "recoger_solicitud"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            # Se confirma de paso la vivienda, para que el vecino corrija lo que el STT oyó mal
            vivienda = estado.get("vivienda_etiqueta")
            saludo = (
                render(
                    "recoger_solicitud.saludo_con_nombre",
                    nombre=estado.get("nombre_declarado"),
                    vivienda=vivienda,
                )
                if estado.get("nombre_declarado")
                else render("recoger_solicitud.saludo", vivienda=vivienda)
            )
            return _preguntar(estado, nodo, render("recoger_solicitud.preguntar", saludo=saludo))
        interp, historial, extra, salida = await _turno(estado, nodo)
        if salida:
            return salida
        pend = _fusionar_pendientes(estado, interp)
        if not pend.get("problema"):
            # Aquí lo que diga el vecino es la solicitud, aunque el modelo no lo haya marcado
            pend["problema"] = entrada.strip()
        else:
            pend["problema"] = entrada.strip()  # la descripción literal manda; el resumen va aparte
        return _avanzar(
            estado, "siguiente_paso", historial=historial, pendientes=pend, confirmado=None, **extra
        )

    async def clasificar_problema(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "clasificar_problema"
        catalogo = _catalogo(estado)
        validaciones = list(estado.get("validaciones", []))
        categoria = estado.get("categoria")
        urgencia = estado.get("urgencia_declarada")
        resumen = estado.get("resumen")
        tiempos = dict(estado.get("tiempos_turno", {}))
        senales = dict(estado.get("senales_confianza") or {})
        if not categoria:
            t0 = perf_counter()
            extraido = await deps.llm.extraer(
                sistema_clasificacion(catalogo),
                estado.get("solicitud") or "",
                esquema_clasificacion(catalogo),
            )
            ms = int((perf_counter() - t0) * 1000)
            tiempos = _tiempos(estado, clasificacion_ms=ms, llm_llamadas=1)
            categoria, urgencia, resumen = (
                extraido.get("categoria"),
                extraido.get("urgencia"),
                extraido.get("resumen"),
            )
            senales = {
                "modelo": deps.llm.nombre,
                "version_prompt": VERSION_CLASIFICACION,
                "urgencia_valida": urgencia in URGENCIAS,
            }
        if categoria not in catalogo:
            validaciones.append("categoria_fuera_de_catalogo->no_clasificable")
            categoria = CATEGORIA_NO_CLASIFICABLE
        else:
            validaciones.append("categoria_en_catalogo")
        if urgencia not in URGENCIAS:
            validaciones.append("urgencia_invalida->media")
            urgencia = "media"
        senales["categoria_en_catalogo"] = categoria != CATEGORIA_NO_CLASIFICABLE
        return _avanzar(
            estado,
            "determinar_urgencia",
            categoria=categoria,
            urgencia_declarada=urgencia,
            resumen=(resumen or estado.get("solicitud") or "").strip(),
            senales_confianza=senales,
            validaciones=validaciones,
            tiempos_turno=tiempos,
            traza=_con_traza(estado, nodo, "clasificado", f"{categoria} / {urgencia}"),
        )

    def _merece_detalle(estado: EstadoLlamada) -> bool:
        """Una descripción corta se beneficia de una pregunta de detalle; una larga no."""
        solicitud = estado.get("solicitud") or ""
        return len(solicitud.split()) < 18

    async def pedir_detalle(estado: EstadoLlamada) -> dict[str, Any]:
        """Un solo turno para concretar la incidencia (dónde, cuántos, desde cuándo, cómo falla)."""
        nodo = "pedir_detalle"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            pregunta = _PREGUNTAS_DETALLE.get(
                estado.get("categoria") or "", _PREGUNTAS_DETALLE["_"]
            )
            return _preguntar(estado, nodo, pregunta, detalle_pedido=True)
        interp, historial, extra, salida = await _turno(estado, nodo)
        if salida:
            return salida
        detalle = entrada.strip()
        if _respuesta_si_no(entrada) is not None and len(entrada.split()) <= 3:
            detalle = ""  # "no", "no sé": sin detalle útil
        solicitud = estado.get("solicitud") or ""
        if detalle:
            solicitud = f"{solicitud} Detalle: {detalle}"
        resumen = estado.get("resumen") or ""
        if interp.problema and interp.problema.lower() not in resumen.lower():
            resumen = f"{resumen}; {interp.problema}" if resumen else interp.problema
        return _avanzar(
            estado,
            "siguiente_paso",
            historial=historial,
            solicitud=solicitud,
            resumen=resumen,
            detalle_pedido=True,
            traza=_con_traza(estado, nodo, "detalle", detalle[:60]),
            **extra,
        )

    async def determinar_urgencia(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "determinar_urgencia"
        cfg = _config(estado)
        declarada = estado.get("urgencia_declarada") or "media"
        efectiva = cfg.urgencia_efectiva(declarada)  # type: ignore[arg-type]
        detalle = f"{declarada} -> {efectiva}" if declarada != efectiva else efectiva
        # Vuelve al enrutador: puede tocar pedir un detalle antes del resumen
        return _avanzar(
            estado,
            "siguiente_paso",
            urgencia=efectiva,
            traza=_con_traza(estado, nodo, "urgencia_efectiva", detalle),
        )

    async def confirmar_resumen(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "confirmar_resumen"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            categoria = estado.get("categoria")
            # Nunca se le dice al vecino que "no se ha podido clasificar": se registra igual
            texto_cat = (
                render("confirmar_resumen.categoria_no_clasificable")
                if categoria == CATEGORIA_NO_CLASIFICABLE
                else render(
                    "confirmar_resumen.categoria", categoria=categoria_hablada(categoria or "")
                )
            )
            # Se repiten nombre y vivienda para que el vecino pueda corregir lo que el STT oyó mal
            quien = (
                f"{estado.get('nombre_declarado')}, {estado.get('vivienda_etiqueta')}"
                if estado.get("nombre_declarado")
                else estado.get("vivienda_etiqueta")
            )
            return _preguntar(
                estado,
                nodo,
                render(
                    "confirmar_resumen.resumen",
                    quien=quien,
                    resumen=estado.get("resumen"),
                    categoria=_mayuscula_inicial(texto_cat),
                    urgencia=estado.get("urgencia"),
                ),
            )
        interp, historial, extra, salida = await _turno(estado, nodo, si_no=True)
        if salida:
            return salida
        # Correcciones: el vecino rectifica datos en lugar de decir sí/no
        cambios: dict[str, Any] = {}
        pend = dict(estado.get("pendientes", {}))
        # Prioridad: problema > vivienda > finca. Una corrección del problema no toca la finca.
        if interp.nombre and interp.confirma is not True and not interp.problema:
            cambios.update(nombre_declarado=interp.nombre, confirmado=None)
            traza_nombre = _con_traza(estado, nodo, "correccion", f"nombre -> {interp.nombre}")
            if interp.piso or interp.puerta or interp.portal:
                cambios.update(vivienda_id=None, vivienda_etiqueta=None)
                for k in ("portal", "piso", "puerta"):
                    if getattr(interp, k):
                        pend[k] = getattr(interp, k)
            return _avanzar(
                estado,
                "siguiente_paso",
                render("confirmar_resumen.corrigiendo"),
                historial=historial,
                pendientes=pend,
                traza=traza_nombre,
                **cambios,
                **extra,
            )
        if (
            interp.problema
            and interp.confirma is not True
            and _es_correccion_real(interp.problema, estado.get("solicitud") or "", entrada)
        ):
            cambios.update(
                solicitud=None,
                categoria=None,
                urgencia=None,
                urgencia_declarada=None,
                resumen=None,
                confirmado=None,
            )
            pend["problema"] = entrada.strip()
            pend["problema_corto"] = interp.problema
            for k in ("categoria", "urgencia"):
                if getattr(interp, k):
                    pend[k] = getattr(interp, k)
        elif interp.piso or interp.puerta or interp.portal:
            cambios.update(vivienda_id=None, vivienda_etiqueta=None, confirmado=None)
            for k in ("portal", "piso", "puerta"):
                if getattr(interp, k):
                    pend[k] = getattr(interp, k)
        elif (
            interp.direccion
            and interp.confirma is not True
            and await _coincide_finca(interp.direccion)
        ):
            # Solo si la nueva dirección es una finca conocida: evita borrar la finca por un
            # "direccion" espurio del modelo ("es en el garaje")
            cambios.update(
                finca_id=None,
                finca_direccion=None,
                vivienda_id=None,
                vivienda_etiqueta=None,
                confirmado=None,
            )
            pend["direccion"] = interp.direccion
        if cambios:
            return _avanzar(
                estado,
                "siguiente_paso",
                render("confirmar_resumen.corrigiendo"),
                historial=historial,
                pendientes=pend,
                traza=_con_traza(estado, nodo, "correccion", ", ".join(sorted(pend))),
                **cambios,
                **extra,
            )
        if interp.confirma is True:
            return _avanzar(
                estado,
                "validar_datos",
                historial=historial,
                confirmado=True,
                traza=_con_traza(estado, nodo, "confirmado"),
                **extra,
            )
        if interp.confirma is False:
            return _preguntar(
                estado,
                "recoger_solicitud",
                render("confirmar_resumen.repetir_solicitud"),
                historial=historial,
                confirmado=False,
                solicitud=None,
                categoria=None,
                urgencia=None,
                urgencia_declarada=None,
                resumen=None,
                traza=_con_traza(estado, nodo, "rechazado", "vuelve a recoger_solicitud"),
                **extra,
            )
        return _preguntar(
            estado,
            nodo,
            render("confirmar_resumen.confirmar_si_no"),
            historial=historial,
            **extra,
        )

    # ------------------------------------------------------------------ nodos de trabajo y cierre

    async def validar_datos(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "validar_datos"
        cfg = _config(estado)
        validaciones = list(estado.get("validaciones", []))
        problemas: list[str] = []
        if not estado.get("finca_id"):
            problemas.append("sin_finca")
        if not estado.get("vivienda_id"):
            problemas.append("sin_vivienda")
        # Regla de Andreu (2026-08-28): nosotros sustituimos al administrador. Con finca, vivienda
        # y solicitud confirmadas SIEMPRE se crea la incidencia; sin categoría o sin especialista
        # queda pendiente a nombre del administrador, nunca "escapa a lo que puedo gestionar".
        categoria = estado.get("categoria")
        if categoria == CATEGORIA_NO_CLASIFICABLE or categoria not in cfg.catalogo_categorias:
            validaciones.append("sin_categoria->pendiente_administrador")
        elif categoria not in cfg.especialista_por_categoria:
            validaciones.append("sin_especialista->pendiente_administrador")
        if problemas:
            validaciones.extend(f"fallo:{p}" for p in problemas)
            return _avanzar(
                estado,
                "no_clasificable",
                validaciones=validaciones,
                motivo_revision=", ".join(problemas),
                traza=_con_traza(estado, nodo, "validacion_fallida", ", ".join(problemas)),
            )
        validaciones.append("datos_validados")
        return _avanzar(
            estado,
            "asignar_especialista",
            validaciones=validaciones,
            traza=_con_traza(estado, nodo, "validacion_correcta"),
        )

    async def no_clasificable(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "no_clasificable"
        cfg = _config(estado)
        destino = cfg.contacto.correo if cfg.contacto else "administrador"
        await deps.repo.guardar_llamada(
            _llamada_desde(estado, "no_clasificable", estado.get("motivo_revision"))
        )
        return _avanzar(
            estado,
            "fin",
            render("no_clasificable.despedida"),
            resultado="no_clasificable",
            finalizada=True,
            canal_aviso="correo",
            destino_aviso=destino,
            traza=_con_traza(
                estado, nodo, "correo_simulado", f"resumen para revisión -> {destino}"
            ),
        )

    async def derivar_humano(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "derivar_humano"
        cfg = _config(estado)
        destino = cfg.contacto.correo if cfg.contacto else "administrador"
        await deps.repo.guardar_llamada(
            _llamada_desde(estado, "derivada_administrador", "vecino pide persona")
        )
        return _avanzar(
            estado,
            "fin",
            render("derivar_humano.despedida"),
            resultado="derivada_administrador",
            finalizada=True,
            canal_aviso="correo",
            destino_aviso=destino,
            traza=_con_traza(estado, nodo, "correo_simulado", f"derivación -> {destino}"),
        )

    async def despedida(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "despedida"
        await deps.repo.guardar_llamada(_llamada_desde(estado, "abandonada"))
        return _avanzar(
            estado,
            "fin",
            render("despedida.despedida"),
            resultado="abandonada",
            finalizada=True,
            traza=_con_traza(estado, nodo, "llamada_registrada"),
        )

    async def asignar_especialista(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "asignar_especialista"
        cfg = _config(estado)
        finca = await deps.repo.obtener_finca(estado["finca_id"])  # type: ignore[arg-type]
        assert finca is not None
        especialista_id = cfg.especialista_por_categoria.get(estado.get("categoria") or "")
        especialistas = {e.id: e for e in await deps.repo.especialistas_de(finca.administrador_id)}
        especialista = especialistas.get(especialista_id) if especialista_id else None
        if especialista is None:
            # Sin especialista: la incidencia queda pendiente para el administrador
            motivo = (
                f"especialista_inexistente:{especialista_id}"
                if especialista_id
                else "sin_especialista_para_categoria"
            )
            return _avanzar(
                estado,
                "crear_ticket",
                especialista_id=None,
                especialista_nombre=None,
                motivo_revision=motivo,
                validaciones=[*estado.get("validaciones", []), "pendiente_administrador"],
                traza=_con_traza(estado, nodo, "sin_especialista", motivo),
            )
        return _avanzar(
            estado,
            "crear_ticket",
            especialista_id=especialista.id,
            especialista_nombre=especialista.nombre,
            validaciones=[
                *estado.get("validaciones", []),
                "especialista_pertenece_al_administrador",
            ],
            traza=_con_traza(estado, nodo, "asignado", especialista.nombre),
        )

    async def crear_ticket(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "crear_ticket"
        incidencia = Incidencia(
            id=_nuevo_id("inc"),
            llamada_id=estado["llamada_id"],
            finca_id=estado["finca_id"],  # type: ignore[arg-type]
            vivienda_id=estado["vivienda_id"],  # type: ignore[arg-type]
            descripcion=estado.get("solicitud") or "",
            categoria=estado["categoria"],  # type: ignore[arg-type]
            especialista_id=estado.get("especialista_id"),
            urgencia=estado["urgencia"],  # type: ignore[arg-type]
            estado="asignada" if estado.get("especialista_id") else "pendiente",
            creada_en=datetime.now(UTC),
        )
        await deps.repo.guardar_llamada(_llamada_desde(estado, "incidencia_creada"))
        await deps.repo.guardar_incidencia(incidencia)
        return _avanzar(
            estado,
            "avisar_canal",
            incidencia_id=incidencia.id,
            resultado="incidencia_creada",
            traza=_con_traza(estado, nodo, "incidencia_creada", incidencia.id),
        )

    async def avisar_canal(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "avisar_canal"
        cfg = _config(estado)
        regla = cfg.regla_para(estado["urgencia"])  # type: ignore[arg-type]
        canal = regla.canal if regla else "ticket"
        destino_regla = regla.destino if regla else "especialista"
        especialista = estado.get("especialista_nombre")
        # El vecino tiene que salir sabiendo qué va a pasar y quién le contactará. No prometemos
        # hora: nadie ha hablado aún con el profesional (regla de Andreu, 2026-08-28).
        contacto = (
            render("avisar_canal.contacto_telefono")
            if estado.get("telefono_llamante")
            else render("avisar_canal.contacto_generico")
        )
        if especialista is None:
            destino = cfg.contacto.correo if cfg.contacto else "administrador"
            canal = "correo"
            aviso = render("avisar_canal.aviso_sin_especialista", contacto=contacto)
        elif destino_regla == "coordinador":
            destino = cfg.contacto.correo if cfg.contacto else "coordinador"
            aviso = render(
                "avisar_canal.aviso_coordinador",
                especialista=especialista,
                especialista_mayus=_mayuscula_inicial(especialista),
                contacto=contacto,
            )
        else:
            destino = especialista if destino_regla == "especialista" else destino_regla
            aviso = render(
                "avisar_canal.aviso_especialista", especialista=especialista, contacto=contacto
            )
        mensaje = render(
            "avisar_canal.cierre", referencia=estado.get("incidencia_id"), aviso=aviso
        )
        return _avanzar(
            estado,
            "fin",
            mensaje,
            finalizada=True,
            canal_aviso=canal,
            destino_aviso=destino,
            traza=_con_traza(estado, nodo, "aviso_simulado", f"{canal} -> {destino}"),
        )

    return {
        "siguiente_paso": siguiente_paso,
        "identificar_finca": identificar_finca,
        "finca_no_identificada": finca_no_identificada,
        "identificar_vivienda": identificar_vivienda,
        "vivienda_no_identificada": vivienda_no_identificada,
        "recoger_solicitud": recoger_solicitud,
        "clasificar_problema": clasificar_problema,
        "pedir_detalle": pedir_detalle,
        "determinar_urgencia": determinar_urgencia,
        "confirmar_resumen": confirmar_resumen,
        "validar_datos": validar_datos,
        "no_clasificable": no_clasificable,
        "derivar_humano": derivar_humano,
        "despedida": despedida,
        "asignar_especialista": asignar_especialista,
        "crear_ticket": crear_ticket,
        "avisar_canal": avisar_canal,
    }
