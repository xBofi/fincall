"""Conversión de audio entre la red telefónica (μ-law, 8 kHz, mono) y el pipeline (PCM16, 16 kHz).

`audioop` está marcado como obsoleto en Python 3.13, pero el proyecto fija 3.12 y ya lo usa la API
para medir energía. Si se sube de versión, sustituir por `numpy`/`scipy` o el paquete `audioop-lts`.
"""

from __future__ import annotations

import audioop  # noqa: F401  (obsoleto en 3.13; ver docstring)

TASA_TELEFONO = 8000
TASA_PIPELINE = 16000


class Remuestreador:
    """Remuestrea PCM16 mono manteniendo el estado entre trozos para no meter clics."""

    def __init__(self, desde: int, hasta: int) -> None:
        self.desde = desde
        self.hasta = hasta
        self._estado: tuple[int, tuple[tuple[int, int], ...]] | None = None

    def convertir(self, pcm: bytes) -> bytes:
        if self.desde == self.hasta or not pcm:
            return pcm
        salida, self._estado = audioop.ratecv(pcm, 2, 1, self.desde, self.hasta, self._estado)
        return salida


def ulaw_a_pcm16(ulaw: bytes) -> bytes:
    return audioop.ulaw2lin(ulaw, 2)


def pcm16_a_ulaw(pcm: bytes) -> bytes:
    return audioop.lin2ulaw(pcm, 2)


def energia(pcm: bytes) -> int:
    """RMS de PCM16: sirve para saber si el vecino está hablando (barge-in y fin de voz)."""
    try:
        return audioop.rms(pcm, 2)
    except audioop.error:
        return 0


class EntradaTelefono:
    """μ-law 8 kHz de la llamada → PCM16 16 kHz para el STT."""

    def __init__(self, tasa_destino: int = TASA_PIPELINE) -> None:
        self._re = Remuestreador(TASA_TELEFONO, tasa_destino)

    def convertir(self, ulaw: bytes) -> bytes:
        return self._re.convertir(ulaw_a_pcm16(ulaw))


class SalidaTelefono:
    """PCM16 a la tasa del TTS → μ-law 8 kHz en trozos de 20 ms (160 bytes), como espera la red."""

    BYTES_POR_TROZO = 160  # 20 ms a 8 kHz, 1 byte por muestra en μ-law

    def __init__(self, tasa_origen: int) -> None:
        self._re = Remuestreador(tasa_origen, TASA_TELEFONO)
        self._resto = bytearray()

    def convertir(self, pcm: bytes, final: bool = False) -> list[bytes]:
        self._resto.extend(pcm16_a_ulaw(self._re.convertir(pcm)))
        trozos: list[bytes] = []
        while len(self._resto) >= self.BYTES_POR_TROZO:
            trozos.append(bytes(self._resto[: self.BYTES_POR_TROZO]))
            del self._resto[: self.BYTES_POR_TROZO]
        if final and self._resto:
            # μ-law 0xFF es silencio: se rellena el último trozo para no cortar la palabra
            trozos.append(bytes(self._resto) + b"\xff" * (self.BYTES_POR_TROZO - len(self._resto)))
            self._resto.clear()
        return trozos


def duracion_ms(n_bytes: int, tasa: int, bytes_por_muestra: int = 2) -> int:
    return int(n_bytes / bytes_por_muestra / tasa * 1000)
