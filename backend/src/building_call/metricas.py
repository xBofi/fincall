"""Métricas de latencia del pipeline de voz.

La métrica principal es `respuesta_ms`: desde el final del audio del vecino hasta el inicio del
audio del bot. Umbrales fijados por Andreu (2026-08-28):

    <= 700 ms  excelente
    <= 1200 ms objetivo
    <= 1500 ms tolerable
    <= 2000 ms mal
    >  2000 ms muy mal

El servidor mide sus etapas (STT, interpretación LLM, grafo); el navegador aporta las suyas
(VAD/EOU, TTS hasta el primer audio) y el total, porque solo él ve los instantes de audio real.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

UMBRALES_MS: tuple[tuple[int, str], ...] = (
    (700, "excelente"),
    (1200, "objetivo"),
    (1500, "tolerable"),
    (2000, "mal"),
)


def calificar(respuesta_ms: float | None) -> str | None:
    if respuesta_ms is None:
        return None
    for limite, nombre in UMBRALES_MS:
        if respuesta_ms <= limite:
            return nombre
    return "muy_mal"


@dataclass
class TiemposTurno:
    """Tiempos de un turno. Los del servidor los rellena el gestor; los del cliente llegan por API."""

    llamada_id: str
    turno: int
    nodo_inicial: str
    nodo_final: str
    # Servidor
    stt_ms: int | None = None
    interpretacion_ms: int | None = None
    clasificacion_ms: int | None = None
    llm_llamadas: int = 0
    grafo_ms: int | None = None
    servidor_ms: int | None = None
    origen_interpretacion: str | None = None  # determinista | llm | ninguna
    # Cliente
    vad_silencio_ms: int | None = None
    red_ms: int | None = None
    tts_inicio_ms: int | None = None
    respuesta_ms: int | None = None
    stt_cliente: str | None = None  # navegador | servidor
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def calificacion(self) -> str | None:
        return calificar(self.respuesta_ms)

    def como_dict(self) -> dict[str, Any]:
        return {**asdict(self), "calificacion": self.calificacion}


def _p(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    orden = sorted(valores)
    k = max(0, min(len(orden) - 1, round(q * (len(orden) - 1))))
    return orden[k]


def resumen(valores: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(valores),
        "p50": round(median(valores)) if valores else None,
        "p95": _p(valores, 0.95),
        "max": max(valores) if valores else None,
    }


class RegistroMetricas:
    """Guarda los últimos N turnos en memoria y calcula agregados."""

    def __init__(self, capacidad: int = 2000) -> None:
        self._turnos: deque[TiemposTurno] = deque(maxlen=capacidad)
        self._indice: dict[tuple[str, int], TiemposTurno] = {}

    def registrar(self, tiempos: TiemposTurno) -> None:
        if len(self._turnos) == self._turnos.maxlen:
            viejo = self._turnos[0]  # el deque lo va a expulsar: el índice tampoco lo guarda
            self._indice.pop((viejo.llamada_id, viejo.turno), None)
        self._turnos.append(tiempos)
        self._indice[(tiempos.llamada_id, tiempos.turno)] = tiempos

    def completar_cliente(
        self, llamada_id: str, turno: int, datos: dict[str, Any]
    ) -> TiemposTurno | None:
        t = self._indice.get((llamada_id, turno))
        if t is None:
            return None
        for clave in ("vad_silencio_ms", "red_ms", "tts_inicio_ms", "respuesta_ms"):
            if isinstance(datos.get(clave), int | float):
                setattr(t, clave, int(datos[clave]))
        if isinstance(datos.get("stt_cliente"), str):
            t.stt_cliente = datos["stt_cliente"]
        if isinstance(datos.get("extra"), dict):
            t.extra.update(datos["extra"])
        return t

    def turnos(self, llamada_id: str | None = None) -> list[TiemposTurno]:
        return [t for t in self._turnos if llamada_id is None or t.llamada_id == llamada_id]

    def agregados(self, llamada_id: str | None = None) -> dict[str, Any]:
        ts = self.turnos(llamada_id)

        def col(nombre: str) -> list[float]:
            return [float(getattr(t, nombre)) for t in ts if getattr(t, nombre) is not None]

        respuesta = col("respuesta_ms")
        con_llm = [t for t in ts if t.llm_llamadas > 0]
        sin_llm = [t for t in ts if t.llm_llamadas == 0]
        return {
            "turnos": len(ts),
            "respuesta_ms": resumen(respuesta),
            "calificaciones": {
                nombre: sum(1 for v in respuesta if calificar(v) == nombre)
                for nombre in (*[n for _, n in UMBRALES_MS], "muy_mal")
            },
            "servidor_ms": resumen(col("servidor_ms")),
            "servidor_ms_con_llm": resumen(
                [float(t.servidor_ms) for t in con_llm if t.servidor_ms is not None]
            ),
            "servidor_ms_sin_llm": resumen(
                [float(t.servidor_ms) for t in sin_llm if t.servidor_ms is not None]
            ),
            "stt_ms": resumen(col("stt_ms")),
            "interpretacion_ms": resumen(col("interpretacion_ms")),
            "clasificacion_ms": resumen(col("clasificacion_ms")),
            "vad_silencio_ms": resumen(col("vad_silencio_ms")),
            "tts_inicio_ms": resumen(col("tts_inicio_ms")),
            "umbrales_ms": {nombre: limite for limite, nombre in UMBRALES_MS},
        }
