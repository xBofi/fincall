"""Nodos del flujo de llamada.

Reglas (bóveda, 04 Arquitectura): el LLM interpreta y extrae; el código valida y decide.
Cada nodo es una función reutilizable por cualquier grafo del registro.

Naturalidad: cada turno del vecino pasa por una interpretación (determinista si basta, LLM si
no) que extrae todo lo que dijo y una intención global. `siguiente_paso` consume esos datos y
salta las preguntas ya respondidas; las intenciones globales (repetir, corregir, humano,
colgar, fuera de ámbito, pregunta) se atienden desde cualquier nodo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
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

UMBRAL_FINCA = 0.6  # Pendiente: calibrar con datos reales (bóveda, 02 Flujos)


@dataclass
class Dependencias:
    repo: Repositorio
    llm: ModeloLenguaje


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
    palabras = set(t.split())
    si = {_normalizar_basico(s) for s in _SI}
    no = {_normalizar_basico(s) for s in _NO}
    if (palabras & si or t in si) and not (palabras & no):
        return True
    if palabras & no or t.startswith("no"):
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
    return v or None


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
    m_puerta = re.search(
        r"(?:\bpuerta\s+|\bletra\s+)?\b([a-h]|\d{1,2}|izquierda|derecha)\b\s*$", resto.strip()
    )
    puerta = None
    if m_puerta and (m_piso is None or m_puerta.start() > m_piso.start()):
        puerta = _normalizar_puerta(m_puerta.group(1))
    return portal, piso, puerta


def _buscar_vivienda(
    viviendas: list[Vivienda], portal: str | None, piso: str | None, puerta: str | None
) -> Vivienda | None:
    candidatas = [
        v
        for v in viviendas
        if _normalizar_piso(v.piso) == piso and _normalizar_puerta(v.puerta) == puerta
    ]
    if portal is not None:
        candidatas = [v for v in candidatas if _normalizar_portal(v.portal) == portal]
    if len(candidatas) == 1:
        return candidatas[0]
    # Si la finca no tiene portales y el vecino no lo indicó, la coincidencia piso+puerta basta
    sin_portal = [v for v in candidatas if v.portal is None]
    return sin_portal[0] if len(sin_portal) == 1 else None


# --------------------------------------------------------------------------- utilidades de estado


def _con_traza(estado: EstadoLlamada, nodo: str, accion: str, detalle: str = "") -> list[Traza]:
    return [*estado.get("traza", []), Traza(nodo=nodo, accion=accion, detalle=detalle)]


def _preguntar(estado: EstadoLlamada, nodo: str, texto: str, **extra: Any) -> dict[str, Any]:
    # `historial` en extra ya incluye el turno del vecino; el mensaje del bot se añade después.
    base = extra.pop("historial", estado.get("historial", []))
    return {
        "nodo_actual": nodo,
        "esperando_usuario": True,
        "entrada_usuario": None,
        **extra,
        "mensajes_bot": [*estado.get("mensajes_bot", []), texto],
        "historial": [*base, {"rol": "bot", "texto": texto}],
        "ultima_pregunta": texto,
    }


def _avanzar(
    estado: EstadoLlamada, nodo: str, mensaje: str | None = None, **extra: Any
) -> dict[str, Any]:
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


_PREFIJOS_NOMBRE = re.compile(
    r"^(?:hola[, ]*)?(?:soy|me llamo|mi nombre es|aqui)\s+", re.IGNORECASE
)


def _nombre_simple(texto: str) -> str | None:
    """'Soy Marta López' -> 'Marta López' sin LLM; None si la frase no es solo un nombre."""
    limpio = _PREFIJOS_NOMBRE.sub("", texto.strip().strip(".!"))
    return limpio.title() if _es_nombre_simple(limpio) else None


def _es_nombre_simple(texto: str) -> bool:
    t = _normalizar_basico(texto)
    palabras = t.split()
    return (
        1 <= len(palabras) <= 3
        and not any(ch.isdigit() for ch in t)
        and not (
            set(palabras)
            & {"no", "si", "que", "hola", "es", "el", "la", "en", "un", "una", "por", "para"}
        )
    )


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


_FAQ: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("grab", "registr"),
        "Sí: la llamada se registra para gestionar su incidencia y para control de calidad. ",
    ),
    (
        ("tard", "cuando", "plazo", "vend"),
        "El plazo depende del especialista; el administrador le informará del seguimiento. ",
    ),
    (("nombre", "para que"), "Solo necesito su nombre para saber quién comunica la incidencia. "),
    (
        ("quien", "maquina", "robot", "persona"),
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
    t = texto.strip()
    if not (t.startswith("¿") or t.endswith("?")):
        return False
    n = _normalizar_basico(t)
    if any(ch.isdigit() for ch in n):
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
        if nodo == "registrar_nombre" and (nombre := _nombre_simple(entrada)):
            interp = Interpretacion(nombre=nombre, origen="determinista")
            return interp, _tiempos(estado, origen_interpretacion="determinista")
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
            return _preguntar(
                estado,
                nodo,
                f"Claro, le repito. {ultima}",
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
                "Lo siento, solo puedo gestionar incidencias y solicitudes de la finca. " + ultima,
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "fuera_ambito"),
                **extra,
            )
        if interp.intencion == "humano":
            return _avanzar(
                estado,
                "derivar_humano",
                historial=historial,
                traza=_con_traza(estado, nodo, "intencion", "humano"),
                **extra,
            )
        if interp.intencion == "colgar" and not aporta_datos:
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
                        f"He identificado {finca.direccion}. ¿Es correcto?",
                        finca_candidata_id=finca.id,
                        pendientes=pend,
                        traza=traza,
                    )
                return _reintento_finca(
                    estado,
                    "No he encontrado esa dirección entre las fincas gestionadas.",
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
                vivienda = _buscar_vivienda(viviendas, portal, np_, npu)
                for k in ("portal", "piso", "puerta"):
                    pend.pop(k, None)
                traza.append(
                    Traza(
                        nodo=nodo,
                        accion="vivienda",
                        detalle=f"portal={portal} piso={np_} puerta={npu}",
                    )
                )
                if vivienda:
                    cambios.update(vivienda_id=vivienda.id, vivienda_etiqueta=vivienda.etiqueta())
                    validaciones.append("vivienda_existe_en_finca")
                    estado = {**estado, **cambios}  # type: ignore[assignment]
                else:
                    return _reintento_vivienda(estado, pend, traza)
            else:
                return _avanzar(
                    estado, "identificar_vivienda", pendientes=pend, traza=traza, **cambios
                )

        if not estado.get("nombre_declarado"):
            nombre = pend.pop("nombre", None)
            if nombre:
                cambios["nombre_declarado"] = nombre
                traza.append(Traza(nodo=nodo, accion="nombre_registrado", detalle=nombre))
                estado = {**estado, **cambios}  # type: ignore[assignment]
            else:
                return _avanzar(
                    estado,
                    "registrar_nombre",
                    pendientes=pend,
                    traza=traza,
                    validaciones=validaciones,
                    **cambios,
                )

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
            f"{prefijo} ¿Puede repetirme la calle y el número?",
            intentos_finca=intentos,
            finca_candidata_id=None,
            pendientes=pend,
            traza=[
                *traza,
                Traza(nodo="identificar_finca", accion="reintento", detalle=str(intentos)),
            ],
            **(extra or {}),
        )

    def _reintento_vivienda(
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
        return _preguntar(
            estado,
            "identificar_vivienda",
            "No encuentro esa vivienda en la finca. ¿Puede repetirme el piso y la puerta, y el portal si lo hay?",
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
                "Hola, soy el asistente de incidencias de su comunidad. "
                "¿En qué finca se encuentra? Dígame la calle y el número.",
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
                    "Entendido, no es esa.",
                    _fusionar_pendientes(estado, interp),
                    estado.get("traza", []),
                    extra,
                )
            return _preguntar(
                estado,
                nodo,
                "Perdone, ¿es correcta esa finca? Responda sí o no.",
                historial=historial,
                **extra,
            )

        # Sin candidata: primero coincidencia determinista de la frase completa (0 llamadas LLM)
        historial = _registrar_entrada(estado)
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
                f"He identificado {finca.direccion}. ¿Es correcto?",
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
                "No he entendido la dirección de la finca.",
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
                "Lo siento, no consigo identificar la finca; puede que no esté gestionada por este "
                "administrador. Si quiere, cuénteme brevemente qué quería comunicar y quedará registrado.",
            )
        historial = _registrar_entrada(estado)
        llamada = _llamada_desde(
            {**estado, "historial": historial, "solicitud": entrada}, "finca_no_identificada"
        )
        await deps.repo.guardar_llamada(llamada)
        return _avanzar(
            estado,
            "fin",
            "Gracias. He registrado su llamada para que el equipo de calidad la revise. Hasta luego.",
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
                "Gracias. ¿Cuál es su vivienda? Indíqueme portal si lo hay, piso y puerta.",
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
        if not (pend.get("piso") and pend.get("puerta")):
            # El LLM no lo sacó; el regex puede completar
            pend.setdefault("piso", piso) if piso else None
            pend.setdefault("puerta", puerta) if puerta else None
            if portal:
                pend.setdefault("portal", portal)
        if not (pend.get("piso") and pend.get("puerta")):
            return _reintento_vivienda(
                {**estado, "historial": historial},
                {k: v for k, v in pend.items() if k not in ("portal", "piso", "puerta")},
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
                "No he podido identificar correctamente la vivienda. Cuénteme igualmente su solicitud; "
                "enviaré un resumen al administrador con su número de teléfono.",
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
            "Gracias. He enviado el resumen al administrador, que se pondrá en contacto con usted. Hasta luego.",
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

    async def registrar_nombre(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "registrar_nombre"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                f"Perfecto, {estado.get('vivienda_etiqueta')}. ¿Con quién hablo? Dígame su nombre.",
            )
        interp, historial, extra, salida = await _turno(estado, nodo)
        if salida:
            return salida
        pend = _fusionar_pendientes(estado, interp)
        if not pend.get("nombre"):
            return _preguntar(
                estado,
                nodo,
                "Perdone, no he entendido su nombre. ¿Me lo puede repetir?",
                historial=historial,
                pendientes=pend,
                **extra,
            )
        return _avanzar(estado, "siguiente_paso", historial=historial, pendientes=pend, **extra)

    async def recoger_solicitud(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "recoger_solicitud"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            saludo = (
                f"Gracias, {estado.get('nombre_declarado')}. "
                if estado.get("nombre_declarado")
                else ""
            )
            return _preguntar(estado, nodo, f"{saludo}Cuénteme, ¿qué ocurre?")
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

    async def determinar_urgencia(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "determinar_urgencia"
        cfg = _config(estado)
        declarada = estado.get("urgencia_declarada") or "media"
        efectiva = cfg.urgencia_efectiva(declarada)  # type: ignore[arg-type]
        detalle = f"{declarada} -> {efectiva}" if declarada != efectiva else efectiva
        return _avanzar(
            estado,
            "confirmar_resumen",
            urgencia=efectiva,
            traza=_con_traza(estado, nodo, "urgencia_efectiva", detalle),
        )

    async def confirmar_resumen(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "confirmar_resumen"
        entrada = estado.get("entrada_usuario")
        if entrada is None:
            categoria = estado.get("categoria")
            texto_cat = (
                "no he podido clasificarlo"
                if categoria == CATEGORIA_NO_CLASIFICABLE
                else f"lo he clasificado como {categoria}"
            )
            return _preguntar(
                estado,
                nodo,
                f"He entendido lo siguiente: {estado.get('resumen')}. {texto_cat.capitalize()}, con urgencia "
                f"{estado.get('urgencia')}. ¿Es correcto?",
            )
        interp, historial, extra, salida = await _turno(estado, nodo, si_no=True)
        if salida:
            return salida
        # Correcciones: el vecino rectifica datos en lugar de decir sí/no
        cambios: dict[str, Any] = {}
        pend = dict(estado.get("pendientes", {}))
        # Prioridad: problema > vivienda > finca. Una corrección del problema no toca la finca.
        if interp.problema and interp.confirma is not True:
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
                "De acuerdo, lo corrijo.",
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
                "De acuerdo. Cuénteme de nuevo qué ocurre, con más detalle si puede.",
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
            "Perdone, no le he entendido. ¿Es correcto el resumen? Responda sí o no.",
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
        categoria = estado.get("categoria")
        if categoria == CATEGORIA_NO_CLASIFICABLE or categoria not in cfg.catalogo_categorias:
            problemas.append("sin_categoria")
        elif categoria not in cfg.especialista_por_categoria:
            problemas.append("sin_especialista_para_categoria")
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
            "Esta solicitud escapa a lo que puedo gestionar automáticamente. He enviado un resumen al "
            "administrador para que la revise una persona. Gracias por su llamada.",
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
            "De acuerdo. Envío ahora un resumen de su llamada al administrador para que una persona "
            "se ponga en contacto con usted lo antes posible. Gracias, hasta luego.",
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
            "De acuerdo. Gracias por llamar; puede volver a llamar cuando quiera. Hasta luego.",
            resultado="abandonada",
            finalizada=True,
            traza=_con_traza(estado, nodo, "llamada_registrada"),
        )

    async def asignar_especialista(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "asignar_especialista"
        cfg = _config(estado)
        finca = await deps.repo.obtener_finca(estado["finca_id"])  # type: ignore[arg-type]
        assert finca is not None
        especialista_id = cfg.especialista_por_categoria[estado["categoria"]]  # type: ignore[index]
        especialistas = {e.id: e for e in await deps.repo.especialistas_de(finca.administrador_id)}
        especialista = especialistas.get(especialista_id)
        if especialista is None:
            return _avanzar(
                estado,
                "no_clasificable",
                motivo_revision=f"especialista_inexistente:{especialista_id}",
                validaciones=[*estado.get("validaciones", []), "fallo:especialista_inexistente"],
                traza=_con_traza(estado, nodo, "especialista_inexistente", especialista_id),
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
        if destino_regla == "especialista":
            destino = especialista or "especialista"
            aviso = f"Se avisará a {especialista} por {canal}."
        elif destino_regla == "coordinador":
            destino = cfg.contacto.correo if cfg.contacto else "coordinador"
            aviso = f"Por su urgencia, se avisa por {canal} al coordinador del servicio y se asigna a {especialista}."
        else:
            destino = destino_regla
            aviso = f"Se avisará a {especialista} por {canal}."
        mensaje = (
            f"He registrado su incidencia con referencia {estado.get('incidencia_id')}. "
            f"{aviso} Gracias por su llamada, hasta luego."
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
        "registrar_nombre": registrar_nombre,
        "recoger_solicitud": recoger_solicitud,
        "clasificar_problema": clasificar_problema,
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
