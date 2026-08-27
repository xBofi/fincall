"""Nodos del flujo de llamada.

Reglas (bóveda, 04 Arquitectura): el LLM interpreta y extrae; el código valida y decide.
Cada nodo es una función reutilizable por cualquier grafo del registro.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
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
from building_call.repos.base import Repositorio

UMBRAL_FINCA = 0.6  # Pendiente: calibrar con datos reales (bóveda, 02 Flujos)


@dataclass
class Dependencias:
    repo: Repositorio
    llm: ModeloLenguaje


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
        r"\b(calle|c|avenida|avda|av|plaza|pza|paseo|numero|n|del|de|la|el|los|las)\b", " ", texto
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
    "sí",
    "correcto",
    "exacto",
    "vale",
    "claro",
    "afirmativo",
    "eso es",
    "asi es",
    "así es",
    "ok",
}
_NO = {"no", "incorrecto", "negativo", "para nada", "que no", "no es"}


def _respuesta_si_no(texto: str) -> bool | None:
    t = _normalizar(texto)
    palabras = set(t.split())
    if palabras & {_normalizar(s) for s in _SI} or t in {_normalizar(s) for s in _SI}:
        if not (palabras & {_normalizar(s) for s in _NO}):
            return True
    if palabras & {_normalizar(s) for s in _NO} or t.startswith("no"):
        return False
    return None


_PISOS = {
    "bajo": "0",
    "primero": "1",
    "primer": "1",
    "segundo": "2",
    "tercero": "3",
    "tercer": "3",
    "cuarto": "4",
    "quinto": "5",
    "sexto": "6",
    "septimo": "7",
    "octavo": "8",
    "noveno": "9",
    "decimo": "10",
    "atico": "atico",
}


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
    return v.upper() or None


_ORDINALES = "|".join(k for k in _PISOS if k not in {"bajo", "atico"})


def _extraer_vivienda_regex(texto: str) -> tuple[str | None, str | None, str | None]:
    """Respaldo determinista para formatos habituales: 'portal 2', '3º B', 'tercero B'."""
    t = _normalizar_basico(texto)
    m_portal = re.search(r"\bportal\s+(\w+)", t)
    portal = _normalizar_portal(m_portal.group(1)) if m_portal else None
    resto = re.sub(r"\bportal\s+\w+", " ", t)
    m_piso = re.search(rf"\b(\d{{1,2}}|{_ORDINALES}|bajo|atico)\b\s*(?:piso|planta)?", resto)
    piso = _normalizar_piso(m_piso.group(1)) if m_piso else None
    m_puerta = re.search(r"(?:\bpuerta\s+|\bletra\s+)?\b([a-h]|\d{1,2})\b\s*$", resto.strip())
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


def construir_nodos(deps: Dependencias) -> dict[str, Any]:
    """Devuelve {nombre_nodo: función async} para registrarlos en un grafo."""

    async def identificar_finca(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "identificar_finca"
        entrada = estado.get("entrada_usuario")
        historial = _registrar_entrada(estado)
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                "Hola, soy el asistente de incidencias de su comunidad. "
                "¿En qué finca se encuentra? Dígame la calle y el número.",
                historial=historial,
            )

        # Confirmación verbal de una candidata previa
        candidata = estado.get("finca_candidata_id")
        if candidata:
            respuesta = _respuesta_si_no(entrada)
            if respuesta is True:
                finca = await deps.repo.obtener_finca(candidata)
                assert finca is not None
                cfg, cfg_id = await deps.repo.configuracion_efectiva(finca.id)
                return _avanzar(
                    estado,
                    "identificar_vivienda",
                    historial=historial,
                    finca_id=finca.id,
                    finca_direccion=finca.direccion,
                    finca_candidata_id=None,
                    configuracion=cfg.model_dump(),
                    configuracion_id=cfg_id,
                    validaciones=[*estado.get("validaciones", []), "finca_confirmada_verbalmente"],
                    traza=_con_traza(estado, nodo, "finca_confirmada", finca.direccion),
                )
            if respuesta is False:
                return await _reintentar_finca(estado, nodo, historial, "Entendido, no es esa.")
            return _preguntar(
                estado,
                nodo,
                "Perdone, ¿es correcta esa finca? Responda sí o no.",
                historial=historial,
            )

        fincas = await deps.repo.listar_fincas()
        puntuadas = sorted(
            ((_similitud_direccion(entrada, f.direccion), f) for f in fincas),
            key=lambda p: p[0],
            reverse=True,
        )
        if puntuadas and puntuadas[0][0] >= UMBRAL_FINCA:
            puntuacion, finca = puntuadas[0]
            return _preguntar(
                estado,
                nodo,
                f"He identificado {finca.direccion}. ¿Es correcto?",
                historial=historial,
                finca_candidata_id=finca.id,
                traza=_con_traza(
                    estado, nodo, "candidata", f"{finca.direccion} ({puntuacion:.2f})"
                ),
            )
        return await _reintentar_finca(
            estado, nodo, historial, "No he encontrado esa dirección entre las fincas gestionadas."
        )

    async def _reintentar_finca(
        estado: EstadoLlamada, nodo: str, historial: list[dict[str, str]], prefijo: str
    ) -> dict[str, Any]:
        intentos = estado.get("intentos_finca", 0) + 1
        if intentos >= DEFAULTS.intentos_finca:
            return _avanzar(
                estado,
                "finca_no_identificada",
                historial=historial,
                intentos_finca=intentos,
                finca_candidata_id=None,
                traza=_con_traza(estado, nodo, "agotados_intentos", str(intentos)),
            )
        return _preguntar(
            estado,
            nodo,
            f"{prefijo} ¿Puede repetirme la calle y el número?",
            historial=historial,
            intentos_finca=intentos,
            finca_candidata_id=None,
            traza=_con_traza(estado, nodo, "reintento", str(intentos)),
        )

    async def finca_no_identificada(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "finca_no_identificada"
        entrada = estado.get("entrada_usuario")
        historial = _registrar_entrada(estado)
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                "Lo siento, no consigo identificar la finca; puede que no esté gestionada por este "
                "administrador. Si quiere, cuénteme brevemente qué quería comunicar y quedará registrado.",
                historial=historial,
            )
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
        historial = _registrar_entrada(estado)
        cfg = _config(estado)
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                "Gracias. ¿Cuál es su vivienda? Indíqueme portal si lo hay, piso y puerta.",
                historial=historial,
            )

        extraido = await deps.llm.extraer(
            "Extrae la localización de una vivienda a partir de lo que dice un vecino en español. "
            "Devuelve null en los campos que no se mencionen. No inventes datos.",
            entrada,
            {
                "title": "vivienda",
                "type": "object",
                "properties": {
                    "portal": {"type": ["string", "null"]},
                    "piso": {"type": ["string", "null"]},
                    "puerta": {"type": ["string", "null"]},
                },
                "required": ["portal", "piso", "puerta"],
            },
        )
        portal = _normalizar_portal(extraido.get("portal"))
        piso = _normalizar_piso(extraido.get("piso"))
        puerta = _normalizar_puerta(extraido.get("puerta"))
        # Regex solo para formatos (bóveda, 04 Arquitectura): completa lo que el LLM no extrajo
        respaldo = _extraer_vivienda_regex(entrada)
        portal = portal or respaldo[0]
        piso = piso or respaldo[1]
        puerta = puerta or respaldo[2]
        assert estado.get("finca_id")
        viviendas = await deps.repo.viviendas_de(estado["finca_id"])  # type: ignore[arg-type]
        vivienda = _buscar_vivienda(viviendas, portal, piso, puerta) if piso and puerta else None
        traza = _con_traza(
            estado, nodo, "extraccion", f"portal={portal} piso={piso} puerta={puerta}"
        )
        if vivienda:
            return _avanzar(
                estado,
                "registrar_nombre",
                historial=historial,
                vivienda_id=vivienda.id,
                vivienda_etiqueta=vivienda.etiqueta(),
                traza=traza,
                validaciones=[*estado.get("validaciones", []), "vivienda_existe_en_finca"],
            )
        intentos = estado.get("intentos_vivienda", 0) + 1
        if intentos >= cfg.intentos_vivienda:
            return _avanzar(
                estado,
                "vivienda_no_identificada",
                historial=historial,
                intentos_vivienda=intentos,
                traza=traza,
            )
        return _preguntar(
            estado,
            nodo,
            "No encuentro esa vivienda en la finca. ¿Puede repetirme el piso y la puerta, y el portal si lo hay?",
            historial=historial,
            intentos_vivienda=intentos,
            traza=traza,
        )

    async def vivienda_no_identificada(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "vivienda_no_identificada"
        entrada = estado.get("entrada_usuario")
        historial = _registrar_entrada(estado)
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                "No he podido identificar correctamente la vivienda. Cuénteme igualmente su solicitud; "
                "enviaré un resumen al administrador con su número de teléfono.",
                historial=historial,
            )
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
        historial = _registrar_entrada(estado)
        if entrada is None:
            return _preguntar(
                estado,
                nodo,
                f"Perfecto, {estado.get('vivienda_etiqueta')}. ¿Con quién hablo? Dígame su nombre.",
                historial=historial,
            )
        extraido = await deps.llm.extraer(
            "Extrae el nombre con el que se presenta la persona. Devuelve solo el nombre tal como lo dice, "
            "sin títulos ni frases. Si no hay nombre, devuelve null.",
            entrada,
            {
                "title": "nombre",
                "type": "object",
                "properties": {"nombre": {"type": ["string", "null"]}},
                "required": ["nombre"],
            },
        )
        nombre = (extraido.get("nombre") or entrada).strip()
        return _avanzar(
            estado,
            "recoger_solicitud",
            historial=historial,
            nombre_declarado=nombre,
            traza=_con_traza(estado, nodo, "nombre_registrado", nombre),
        )

    async def recoger_solicitud(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "recoger_solicitud"
        entrada = estado.get("entrada_usuario")
        historial = _registrar_entrada(estado)
        if entrada is None:
            saludo = (
                f"Gracias, {estado.get('nombre_declarado')}. "
                if estado.get("nombre_declarado")
                else ""
            )
            return _preguntar(estado, nodo, f"{saludo}Cuénteme, ¿qué ocurre?", historial=historial)
        return _avanzar(
            estado, "clasificar_problema", historial=historial, solicitud=entrada, confirmado=None
        )

    async def clasificar_problema(estado: EstadoLlamada) -> dict[str, Any]:
        nodo = "clasificar_problema"
        cfg = _config(estado)
        catalogo = [*cfg.catalogo_categorias, CATEGORIA_NO_CLASIFICABLE]
        extraido = await deps.llm.extraer(
            "Eres el clasificador de incidencias de una comunidad de propietarios. Clasifica la solicitud "
            f"del vecino en exactamente una categoría del catálogo cerrado: {', '.join(catalogo)}. "
            f"Si no encaja claramente, usa '{CATEGORIA_NO_CLASIFICABLE}'. Estima la urgencia: baja "
            "(molestia sin riesgo), media (afecta al uso normal), alta (daños en curso o servicio esencial "
            "interrumpido), emergencia (riesgo para personas). Resume en una frase en español, en tercera "
            "persona, sin inventar datos.",
            estado.get("solicitud") or "",
            {
                "title": "clasificacion",
                "type": "object",
                "properties": {
                    "categoria": {"type": "string", "enum": catalogo},
                    "urgencia": {"type": "string", "enum": list(URGENCIAS)},
                    "resumen": {"type": "string"},
                },
                "required": ["categoria", "urgencia", "resumen"],
            },
        )
        categoria = extraido.get("categoria")
        urgencia = extraido.get("urgencia")
        validaciones = list(estado.get("validaciones", []))
        if categoria not in catalogo:
            validaciones.append("categoria_fuera_de_catalogo->no_clasificable")
            categoria = CATEGORIA_NO_CLASIFICABLE
        else:
            validaciones.append("categoria_en_catalogo")
        if urgencia not in URGENCIAS:
            validaciones.append("urgencia_invalida->media")
            urgencia = "media"
        senales = {
            "modelo": deps.llm.nombre,
            "categoria_en_catalogo": categoria != CATEGORIA_NO_CLASIFICABLE,
            "urgencia_valida": extraido.get("urgencia") in URGENCIAS,
            "version_prompt": "clasificacion-v1",
        }
        return _avanzar(
            estado,
            "determinar_urgencia",
            categoria=categoria,
            urgencia_declarada=urgencia,
            resumen=(extraido.get("resumen") or estado.get("solicitud") or "").strip(),
            senales_confianza=senales,
            validaciones=validaciones,
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
        historial = _registrar_entrada(estado)
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
                historial=historial,
            )
        respuesta = _respuesta_si_no(entrada)
        if respuesta is None:
            extraido = await deps.llm.extraer(
                "El vecino responde a la pregunta '¿Es correcto?'. Indica si confirma (true), rechaza (false) o no queda claro (null).",
                entrada,
                {
                    "title": "confirmacion",
                    "type": "object",
                    "properties": {"confirma": {"type": ["boolean", "null"]}},
                    "required": ["confirma"],
                },
            )
            respuesta = extraido.get("confirma")
        if respuesta is True:
            return _avanzar(
                estado,
                "validar_datos",
                historial=historial,
                confirmado=True,
                traza=_con_traza(estado, nodo, "confirmado"),
            )
        if respuesta is False:
            return _preguntar(
                estado,
                "recoger_solicitud",
                "De acuerdo. Cuénteme de nuevo qué ocurre, con más detalle si puede.",
                historial=historial,
                confirmado=False,
                solicitud=None,
                traza=_con_traza(estado, nodo, "rechazado", "vuelve a recoger_solicitud"),
            )
        return _preguntar(
            estado,
            nodo,
            "Perdone, no le he entendido. ¿Es correcto el resumen? Responda sí o no.",
            historial=historial,
        )

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
        llamada = _llamada_desde(estado, "no_clasificable", estado.get("motivo_revision"))
        await deps.repo.guardar_llamada(llamada)
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
        ahora = datetime.now(UTC)
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
            creada_en=ahora,
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
        if destino_regla == "especialista":
            destino = estado.get("especialista_nombre") or "especialista"
        elif destino_regla == "coordinador":
            destino = cfg.contacto.correo if cfg.contacto else "coordinador"
        else:
            destino = destino_regla
        especialista = estado.get("especialista_nombre")
        if destino_regla == "coordinador":
            aviso = (
                f"Por su urgencia, se avisa por {canal} al coordinador del servicio y se asigna "
                f"a {especialista}."
            )
        else:
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
        "asignar_especialista": asignar_especialista,
        "crear_ticket": crear_ticket,
        "avisar_canal": avisar_canal,
    }
