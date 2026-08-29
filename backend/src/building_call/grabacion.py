"""Grabación de la llamada completa: las dos voces sobre un mismo reloj.

Hasta 2026-08-29 el expediente sonaba como un monólogo del vecino:

- la consola web guardaba solo la pista `cliente`, porque el bot habla por el navegador
  (Web Speech API) y el servidor nunca ve ese audio;
- además la descartaba mientras el asistente hablaba (`if not pausado`), así que ni siquiera esa
  pista respetaba los tiempos: los silencios desaparecían y la llamada quedaba comprimida;
- la telefonía sí capturaba las dos, pero como dos WAV independientes —el vecino en continuo y el
  bot como una concatenación de turnos sin huecos—, imposibles de escuchar a la vez.

Este módulo coloca las dos voces sobre una línea de tiempo común que arranca con la llamada, y
deja tres pistas alineadas: `cliente`, `asistente` y `mezcla` (la suma, que es la que se escucha
para seguir la conversación).

Sobre la fidelidad del canal del asistente: en telefonía son los mismos bytes que salen a la
línea, es una captura. En la consola web es una *reconstrucción*: se sintetiza con el TTS de
servidor el texto que el navegador informa como realmente entregado (`texto_entregado`, que ya
descuenta lo que un barge-in cortó). Sirve para seguir la conversación; no es prueba de la voz
que oyó el vecino, que era la del navegador.
"""

from __future__ import annotations

import audioop
from collections.abc import Callable
from time import perf_counter

TASA = 16000
"""Todas las pistas se guardan a 16 kHz PCM16 mono, sea cual sea el origen."""

HUECO_MINIMO_MS = 500
"""Un canal continuo se escribe seguido; solo se rellena silencio si el reloj se ha ido más de
esto por delante de lo escrito. Con menos, el jitter de red haría crecer la pista sin parar."""

CANALES = ("cliente", "asistente")

EstadoRatecv = tuple[int, tuple[tuple[int, int], ...]] | None
"""Estado interno de `audioop.ratecv`, que se arrastra de un trozo al siguiente."""


class GrabadorConversacion:
    """Acumula las dos voces posicionadas en el tiempo desde el inicio de la llamada."""

    def __init__(
        self,
        tasa: int = TASA,
        reloj: Callable[[], float] = perf_counter,
        hueco_minimo_ms: int = HUECO_MINIMO_MS,
    ) -> None:
        self.tasa = tasa
        self._reloj = reloj
        self._t0 = reloj()
        self._hueco = int(tasa * hueco_minimo_ms / 1000) * 2
        self._pistas: dict[str, bytearray] = {c: bytearray() for c in CANALES}
        self._estados: dict[tuple[str, int], EstadoRatecv] = {}

    # ------------------------------------------------------------------ tiempo

    def marcar(self) -> int:
        """Posición actual en la línea de tiempo, en bytes. Sirve para anclar un turno del bot
        al momento en que empezó a sonar, aunque su audio se sintetice después."""
        muestras = int(max(self._reloj() - self._t0, 0.0) * self.tasa)
        return muestras * 2

    # ------------------------------------------------------------------ escritura

    def escribir_cliente(self, pcm: bytes, tasa_origen: int | None = None) -> None:
        """Audio del vecino, en el orden en que llega. Siempre: también mientras habla el bot,
        porque si no la pista pierde los huecos y deja de coincidir con el reloj."""
        self._colocar("cliente", pcm, tasa_origen, desde=None)

    def escribir_asistente(
        self, pcm: bytes, tasa_origen: int | None = None, desde: int | None = None
    ) -> None:
        """Audio del bot. `desde` es la marca de cuándo empezó a sonar; sin ella se coloca ahora."""
        self._colocar("asistente", pcm, tasa_origen, desde=desde)

    def _colocar(self, canal: str, pcm: bytes, tasa_origen: int | None, desde: int | None) -> None:
        if not pcm:
            return
        pcm = self._remuestrear(canal, pcm, tasa_origen or self.tasa)
        buffer = self._pistas[canal]
        if desde is None:
            reloj = self.marcar()
            # Solo se respeta el reloj si hay un hueco de verdad; si no, se escribe seguido.
            inicio = reloj if reloj - len(buffer) > self._hueco else len(buffer)
        else:
            inicio = max(desde, len(buffer))  # nunca se pisa un turno ya escrito
        inicio -= inicio % 2  # alineado a muestra
        if inicio > len(buffer):
            buffer.extend(b"\x00" * (inicio - len(buffer)))
        buffer.extend(pcm)

    def _remuestrear(self, canal: str, pcm: bytes, desde: int) -> bytes:
        if desde == self.tasa:
            return pcm
        clave = (canal, desde)
        # El estado se conserva entre trozos: un remuestreador nuevo por trozo mete clics.
        salida, self._estados[clave] = audioop.ratecv(
            pcm, 2, 1, desde, self.tasa, self._estados.get(clave)
        )
        return salida

    # ------------------------------------------------------------------ salida

    def mezcla(self) -> bytes:
        """Las dos voces sumadas. `audioop.add` satura en los límites de int16 en lugar de dar la
        vuelta, así que si coinciden dos voces fuertes recorta pero no chasquea."""
        cliente = bytes(self._pistas["cliente"])
        asistente = bytes(self._pistas["asistente"])
        largo = max(len(cliente), len(asistente))
        if not largo:
            return b""
        cliente += b"\x00" * (largo - len(cliente))
        asistente += b"\x00" * (largo - len(asistente))
        return audioop.add(cliente, asistente, 2)

    def pistas(self) -> dict[str, bytes]:
        """Pistas no vacías. `mezcla` va primero: es la que se escucha para seguir la llamada."""
        salida: dict[str, bytes] = {}
        if (mezcla := self.mezcla()) and any(self._pistas[c] for c in CANALES):
            salida["mezcla"] = mezcla
        for canal in CANALES:
            if self._pistas[canal]:
                salida[canal] = bytes(self._pistas[canal])
        return salida

    def duracion_ms(self) -> int:
        largo = max(len(self._pistas[c]) for c in CANALES)
        return int(largo / 2 / self.tasa * 1000)
