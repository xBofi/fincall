"""Grabación de la llamada completa: las dos voces sobre un mismo reloj.

Hasta 2026-08-29 el expediente sonaba como un monólogo del vecino:

- la consola web guardaba solo la pista `client`, porque el bot habla por el navegador
  (Web Speech API) y el servidor nunca ve ese audio;
- además la descartaba mientras el asistente hablaba (`if not pausado`), así que ni siquiera esa
  pista respetaba los tiempos: los silencios desaparecían y la llamada quedaba comprimida;
- la telefonía sí capturaba las dos, pero como dos WAV independientes —el vecino en continuo y el
  bot como una concatenación de turnos sin huecos—, imposibles de escuchar a la vez.

Este módulo coloca las dos voces sobre una línea de tiempo común que arranca con la llamada, y
deja tres pistas alineadas: `client`, `assistant` y `mix` (la suma, que es la que se escucha
para seguir la conversación).

Sobre la fidelidad del canal del asistente: en telefonía son los mismos bytes que salen a la
línea, es una captura. En la consola web es una *reconstrucción*: se sintetiza con el TTS de
servidor el texto que el navegador informa como realmente entregado (`delivered_text`, que ya
descuenta lo que un barge-in cortó). Sirve para seguir la conversación; no es prueba de la voz
que oyó el vecino, que era la del navegador.
"""

from __future__ import annotations

import audioop
from collections.abc import Callable
from time import perf_counter

RATE = 16000
"""Todas las pistas se guardan a 16 kHz PCM16 mono, sea cual sea el origen."""

MIN_GAP_MS = 500
"""Un canal continuo se escribe seguido; solo se rellena silencio si el reloj se ha ido más de
esto por delante de lo escrito. Con menos, el jitter de red haría crecer la pista sin parar."""

CHANNELS = ("client", "assistant")

RatecvState = tuple[int, tuple[tuple[int, int], ...]] | None
"""Estado interno de `audioop.ratecv`, que se arrastra de un trozo al siguiente."""


class ConversationRecorder:
    """Acumula las dos voces posicionadas en el tiempo desde el inicio de la llamada."""

    def __init__(
        self,
        rate: int = RATE,
        clock: Callable[[], float] = perf_counter,
        min_gap_ms: int = MIN_GAP_MS,
    ) -> None:
        self.rate = rate
        self._clock = clock
        self._t0 = clock()
        self._gap = int(rate * min_gap_ms / 1000) * 2
        self._tracks: dict[str, bytearray] = {c: bytearray() for c in CHANNELS}
        self._states: dict[tuple[str, int], RatecvState] = {}

    # ------------------------------------------------------------------ tiempo

    def mark(self) -> int:
        """Posición actual en la línea de tiempo, en bytes. Sirve para anclar un turno del bot
        al momento en que empezó a sonar, aunque su audio se sintetice después."""
        samples = int(max(self._clock() - self._t0, 0.0) * self.rate)
        return samples * 2

    # ------------------------------------------------------------------ escritura

    def write_client(self, pcm: bytes, source_rate: int | None = None) -> None:
        """Audio del vecino, en el orden en que llega. Siempre: también mientras habla el bot,
        porque si no la pista pierde los huecos y deja de coincidir con el reloj."""
        self._place("client", pcm, source_rate, since=None)

    def write_assistant(
        self, pcm: bytes, source_rate: int | None = None, since: int | None = None
    ) -> None:
        """Audio del bot. `since` es la marca de cuándo empezó a sonar; sin ella se coloca ahora."""
        self._place("assistant", pcm, source_rate, since=since)

    def _place(self, channel: str, pcm: bytes, source_rate: int | None, since: int | None) -> None:
        if not pcm:
            return
        pcm = self._resample(channel, pcm, source_rate or self.rate)
        buffer = self._tracks[channel]
        if since is None:
            clock = self.mark()
            # Solo se respeta el reloj si hay un hueco de verdad; si no, se escribe seguido.
            start = clock if clock - len(buffer) > self._gap else len(buffer)
        else:
            start = max(since, len(buffer))  # nunca se pisa un turno ya escrito
        start -= start % 2  # alineado a muestra
        if start > len(buffer):
            buffer.extend(b"\x00" * (start - len(buffer)))
        buffer.extend(pcm)

    def _resample(self, channel: str, pcm: bytes, since: int) -> bytes:
        if since == self.rate:
            return pcm
        key = (channel, since)
        # El estado se conserva entre trozos: un remuestreador nuevo por trozo mete clics.
        output, self._states[key] = audioop.ratecv(
            pcm, 2, 1, since, self.rate, self._states.get(key)
        )
        return output

    # ------------------------------------------------------------------ salida

    def mix(self) -> bytes:
        """Las dos voces sumadas. `audioop.add` satura en los límites de int16 en lugar de dar la
        vuelta, así que si coinciden dos voces fuertes recorta pero no chasquea."""
        client = bytes(self._tracks["client"])
        assistant = bytes(self._tracks["assistant"])
        long = max(len(client), len(assistant))
        if not long:
            return b""
        client += b"\x00" * (long - len(client))
        assistant += b"\x00" * (long - len(assistant))
        return audioop.add(client, assistant, 2)

    def tracks(self) -> dict[str, bytes]:
        """Pistas no vacías. `mix` va primero: es la que se escucha para seguir la llamada."""
        output: dict[str, bytes] = {}
        if (mix := self.mix()) and any(self._tracks[c] for c in CHANNELS):
            output["mix"] = mix
        for channel in CHANNELS:
            if self._tracks[channel]:
                output[channel] = bytes(self._tracks[channel])
        return output

    def duration_ms(self) -> int:
        long = max(len(self._tracks[c]) for c in CHANNELS)
        return int(long / 2 / self.rate * 1000)
