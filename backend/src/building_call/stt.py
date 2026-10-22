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
class Transcript:
    text: str
    ms: int
    model: str
    detail: dict[str, Any]


class VoiceRecognizer(Protocol):
    name: str

    async def transcribe(self, audio: bytes, mime: str) -> Transcript: ...

    def available(self) -> bool: ...


class UnavailableRecognizer:
    name = "none"

    def available(self) -> bool:
        return False

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        raise RuntimeError(
            "No hay reconocimiento de voz configurado en el servidor (BC_STT=whisper)"
        )


class WhisperRecognizer:
    """faster-whisper. Modelos recomendados para español y latencia: `small`, `base`, `turbo`."""

    def __init__(
        self, model: str = "small", device: str = "auto", compute_type: str = "auto"
    ) -> None:
        self.name = f"faster-whisper/{model}"
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return self._error is None

    async def _load(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                def make() -> Any:
                    return WhisperModel(
                        self._model_name,
                        device=self._device,
                        compute_type=self._compute_type,
                    )

                try:
                    self._model = await asyncio.to_thread(make)
                except Exception as e:  # noqa: BLE001
                    self._error = str(e)
                    raise
        return self._model

    async def warm_up(self) -> None:
        try:
            await self._load()
        except Exception:  # noqa: BLE001
            pass

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        model = await self._load()
        t0 = perf_counter()

        def run() -> tuple[str, dict[str, Any]]:
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language="es",
                beam_size=1,
                best_of=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return text, {
                "audio_duration_s": round(info.duration, 2),
                "language_prob": round(info.language_probability, 2),
            }

        text, detail = await asyncio.to_thread(run)
        return Transcript(
            text=text, ms=int((perf_counter() - t0) * 1000), model=self.name, detail=detail
        )
