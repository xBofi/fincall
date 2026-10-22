"""Conversión de audio entre la red telefónica (μ-law, 8 kHz, mono) y el pipeline (PCM16, 16 kHz).

`audioop` está marcado como obsoleto en Python 3.13, pero el proyecto fija 3.12 y ya lo usa la API
para medir energía. Si se sube de versión, sustituir por `numpy`/`scipy` o el paquete `audioop-lts`.
"""

from __future__ import annotations

import audioop  # noqa: F401  (obsoleto en 3.13; ver docstring)

PHONE_RATE = 8000
PIPELINE_RATE = 16000


class Resampler:
    """Remuestrea PCM16 mono manteniendo el estado entre trozos para no meter clics."""

    def __init__(self, since: int, hasta: int) -> None:
        self.since = since
        self.hasta = hasta
        self._state: tuple[int, tuple[tuple[int, int], ...]] | None = None

    def convert(self, pcm: bytes) -> bytes:
        if self.since == self.hasta or not pcm:
            return pcm
        output, self._state = audioop.ratecv(pcm, 2, 1, self.since, self.hasta, self._state)
        return output


def ulaw_to_pcm16(ulaw: bytes) -> bytes:
    return audioop.ulaw2lin(ulaw, 2)


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    return audioop.lin2ulaw(pcm, 2)


def energy(pcm: bytes) -> int:
    """RMS de PCM16: sirve para saber si el vecino está hablando (barge-in y fin de voz)."""
    try:
        return audioop.rms(pcm, 2)
    except audioop.error:
        return 0


class PhoneInput:
    """μ-law 8 kHz de la llamada → PCM16 16 kHz para el STT."""

    def __init__(self, target_rate: int = PIPELINE_RATE) -> None:
        self._re = Resampler(PHONE_RATE, target_rate)

    def convert(self, ulaw: bytes) -> bytes:
        return self._re.convert(ulaw_to_pcm16(ulaw))


class PhoneOutput:
    """PCM16 a la tasa del TTS → μ-law 8 kHz en trozos de 20 ms (160 bytes), como espera la red."""

    BYTES_PER_CHUNK = 160  # 20 ms a 8 kHz, 1 byte por muestra en μ-law

    def __init__(self, source_rate: int) -> None:
        self._re = Resampler(source_rate, PHONE_RATE)
        self._remainder = bytearray()

    def convert(self, pcm: bytes, final: bool = False) -> list[bytes]:
        self._remainder.extend(pcm16_to_ulaw(self._re.convert(pcm)))
        chunks: list[bytes] = []
        while len(self._remainder) >= self.BYTES_PER_CHUNK:
            chunks.append(bytes(self._remainder[: self.BYTES_PER_CHUNK]))
            del self._remainder[: self.BYTES_PER_CHUNK]
        if final and self._remainder:
            # μ-law 0xFF es silencio: se rellena el último trozo para no cortar la palabra
            chunks.append(bytes(self._remainder) + b"\xff" * (self.BYTES_PER_CHUNK - len(self._remainder)))
            self._remainder.clear()
        return chunks


def duration_ms(n_bytes: int, rate: int, bytes_per_sample: int = 2) -> int:
    return int(n_bytes / bytes_per_sample / rate * 1000)
