"""Reconocimiento de voz en el servidor (opcional).

Proveedor local `faster-whisper` (CTranslate2). Se importa de forma perezosa para que el
backend funcione sin él: el navegador puede usar su propio reconocimiento (Web Speech API).
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol


@dataclass
class Transcripcion:
    texto: str
    ms: int
    modelo: str
    detalle: dict[str, Any]


class ReconocedorVoz(Protocol):
    nombre: str

    async def transcribir(self, audio: bytes, mime: str) -> Transcripcion: ...

    def disponible(self) -> bool: ...


class ReconocedorNoDisponible:
    nombre = "ninguno"

    def disponible(self) -> bool:
        return False

    async def transcribir(self, audio: bytes, mime: str) -> Transcripcion:
        raise RuntimeError(
            "No hay reconocimiento de voz configurado en el servidor (BC_STT=whisper)"
        )


class ReconocedorWhisper:
    """faster-whisper. Modelos recomendados para español y latencia: `small`, `base`, `turbo`."""

    def __init__(
        self, modelo: str = "small", dispositivo: str = "auto", tipo_computo: str = "auto"
    ) -> None:
        self.nombre = f"faster-whisper/{modelo}"
        self._modelo_nombre = modelo
        self._dispositivo = dispositivo
        self._tipo_computo = tipo_computo
        self._modelo: Any = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    def disponible(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return self._error is None

    async def _cargar(self) -> Any:
        if self._modelo is not None:
            return self._modelo
        async with self._lock:
            if self._modelo is None:
                from faster_whisper import WhisperModel

                def crear() -> Any:
                    return WhisperModel(
                        self._modelo_nombre,
                        device=self._dispositivo,
                        compute_type=self._tipo_computo,
                    )

                try:
                    self._modelo = await asyncio.to_thread(crear)
                except Exception as e:  # noqa: BLE001
                    self._error = str(e)
                    raise
        return self._modelo

    async def calentar(self) -> None:
        try:
            await self._cargar()
        except Exception:  # noqa: BLE001
            pass

    async def transcribir(self, audio: bytes, mime: str) -> Transcripcion:
        modelo = await self._cargar()
        t0 = perf_counter()

        def ejecutar() -> tuple[str, dict[str, Any]]:
            segmentos, info = modelo.transcribe(
                io.BytesIO(audio),
                language="es",
                beam_size=1,
                best_of=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            texto = " ".join(s.text.strip() for s in segmentos).strip()
            return texto, {
                "duracion_audio_s": round(info.duration, 2),
                "prob_idioma": round(info.language_probability, 2),
            }

        texto, detalle = await asyncio.to_thread(ejecutar)
        return Transcripcion(
            texto=texto, ms=int((perf_counter() - t0) * 1000), modelo=self.nombre, detalle=detalle
        )
