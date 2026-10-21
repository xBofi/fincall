"""Estado de una llamada dentro del grafo.

Cada turno de conversación es una invocación del grafo: el estado entra con `entrada_usuario`
y sale con `mensajes_bot` y `esperando_usuario=True` (o `finalizada=True`). El grafo decide el
siguiente nodo a partir de `nodo_actual`; el LLM nunca elige nodos.

`pendientes` guarda datos que el vecino dijo antes de que se le preguntaran (interpretación de
turno); `siguiente_paso` los consume y salta las preguntas ya respondidas.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Traza(TypedDict):
    nodo: str
    accion: str
    detalle: str


class EstadoLlamada(TypedDict, total=False):
    llamada_id: str
    iniciada_en: str
    telefono_llamante: str | None
    turno: int
    nodo_actual: str
    entrada_usuario: str | None
    ultima_pregunta: str | None
    # Lo último que dijo (o intentó decir) el bot y cuánto de ello llegó a sonar (ledger de salida)
    ultimo_mensaje_bot: str | None
    entrega_ultima: dict[str, Any] | None
    # Frases del último mensaje con su importancia, y los avisos que salieron en este turno
    piezas_bot: list[dict[str, Any]]
    avisos_turno: list[str]
    # Turno de detalle del problema ya hecho (o no procede)
    detalle_pedido: bool
    mensajes_bot: list[str]
    historial: list[dict[str, str]]
    traza: list[Traza]
    validaciones: list[str]
    esperando_usuario: bool
    finalizada: bool
    # Tiempos del turno en curso (los rellenan los nodos; el gestor los registra)
    tiempos_turno: dict[str, Any]
    # Última interpretación LLM/determinista del turno (para la consola)
    interpretacion: dict[str, Any] | None
    # Datos dichos por el vecino aún no consumidos por su nodo
    pendientes: dict[str, str]

    # Equipos de la finca (cargados al confirmarla) y si la clasificación ya los tuvo delante
    equipos: list[dict[str, Any]]
    equipos_evaluados: bool
    # A quién va la incidencia: equipo afectado, tipo de actuación y pega del vecino al resumen
    equipo_id: str | None
    actuacion: str | None
    objecion: str | None
    # Para el administrador o el profesional: equipo, contrato y por qué le llega
    sugerencia: str | None

    # Configuración efectiva resuelta al identificar la finca
    configuracion: dict[str, Any] | None
    configuracion_id: str | None
    grafo: str

    # Identificación
    finca_id: str | None
    finca_direccion: str | None
    finca_candidata_id: str | None
    intentos_finca: int
    vivienda_id: str | None
    vivienda_etiqueta: str | None
    intentos_vivienda: int
    nombre_declarado: str | None

    # Solicitud
    solicitud: str | None
    categoria: str | None
    urgencia: str | None
    urgencia_declarada: str | None
    resumen: str | None
    senales_confianza: dict[str, Any] | None
    confirmado: bool | None

    # Resolución
    especialista_id: str | None
    especialista_nombre: str | None
    canal_aviso: str | None
    destino_aviso: str | None
    incidencia_id: str | None
    resultado: str | None
    motivo_revision: str | None


def estado_inicial(llamada_id: str, iniciada_en: str, grafo: str) -> EstadoLlamada:
    return EstadoLlamada(
        llamada_id=llamada_id,
        iniciada_en=iniciada_en,
        telefono_llamante=None,
        turno=0,
        nodo_actual="siguiente_paso",
        entrada_usuario=None,
        ultima_pregunta=None,
        ultimo_mensaje_bot=None,
        entrega_ultima=None,
        piezas_bot=[],
        avisos_turno=[],
        detalle_pedido=False,
        mensajes_bot=[],
        historial=[],
        traza=[],
        validaciones=[],
        esperando_usuario=False,
        finalizada=False,
        tiempos_turno={},
        interpretacion=None,
        pendientes={},
        equipos=[],
        equipos_evaluados=False,
        equipo_id=None,
        actuacion=None,
        objecion=None,
        sugerencia=None,
        configuracion=None,
        configuracion_id=None,
        grafo=grafo,
        finca_id=None,
        finca_direccion=None,
        finca_candidata_id=None,
        intentos_finca=0,
        vivienda_id=None,
        vivienda_etiqueta=None,
        intentos_vivienda=0,
        nombre_declarado=None,
        solicitud=None,
        categoria=None,
        urgencia=None,
        urgencia_declarada=None,
        resumen=None,
        senales_confianza=None,
        confirmado=None,
        especialista_id=None,
        especialista_nombre=None,
        canal_aviso=None,
        destino_aviso=None,
        incidencia_id=None,
        resultado=None,
        motivo_revision=None,
    )
