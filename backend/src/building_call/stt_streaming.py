"""STT en streaming con AssemblyAI (Universal-Streaming v3) sobre el endpoint EUROPEO.

El navegador envía PCM16 al backend por WebSocket; el backend reenvía a AssemblyAI y recibe
turnos con detección de fin de turno del proveedor. La clave nunca sale del servidor.

Medido (2026-08-29, WAV sintéticos en español): fin de voz real -> transcript final formateado
≈ 500 ms; conexión inicial ≈ 1,1-1,2 s (se abre una vez por llamada). Trozos de audio entre 50 y
1000 ms obligatorios: se agrupan antes de enviar.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

import websockets

HOST_STREAMING_EU = "streaming.eu.assemblyai.com"


class AssemblyAIConfigError(ValueError):
    pass


def validate_eu_endpoint(url: str) -> str:
    """Solo se permiten endpoints europeos (decisión de producto, 2026-08-28)."""
    if ".eu.assemblyai.com" not in url:
        raise AssemblyAIConfigError(
            f"Endpoint AssemblyAI no europeo rechazado: {url}. Use https://api.eu.assemblyai.com"
        )
    return url.rstrip("/")


@dataclass
class TurnEvent:
    kind: str  # parcial | final | error
    text: str = ""
    received_at: float = field(default_factory=perf_counter)
    detail: dict[str, Any] = field(default_factory=dict)


class AssemblyAIStreamingSession:
    """Una sesión por llamada. `send_audio` acepta PCM16 mono; `eventos()` produce turnos."""

    def __init__(
        self,
        api_key: str,
        sample_rate: int = 16000,
        host: str = HOST_STREAMING_EU,
        min_chunk_ms: int = 100,
        eot_confidence: float = 0.7,
        eot_silence_ms: int = 220,
        max_silence_ms: int = 800,
        keyterms: list[str] | None = None,
        language: str | list[str] | None = None,
        speech_model: str = "",
        language_detection: bool = False,
    ) -> None:
        """Fin de turno: AssemblyAI cierra el turno cuando su confianza de que la frase ha
        terminado supera `eot_confidence` y hay `eot_silence_ms` de silencio; si no está
        seguro, espera `max_silence_ms`. Los valores por defecto del proveedor (0.7 / 160 ms)
        cortaban al vecino a mitad de frase (prueba manual, 2026-08-28)."""
        if not api_key:
            raise AssemblyAIConfigError("Falta BC_ASSEMBLYAI_API_KEY")
        if not host.endswith(".eu.assemblyai.com"):
            raise AssemblyAIConfigError(f"Host de streaming no europeo rechazado: {host}")
        self.api_key = api_key
        self.sample_rate = sample_rate
        params: dict[str, Any] = {
            "sample_rate": sample_rate,
            "encoding": "pcm_s16le",
            "format_turns": "true",
            "end_of_turn_confidence_threshold": eot_confidence,
            "min_end_of_turn_silence_when_confident": eot_silence_ms,
            "max_turn_silence": max_silence_ms,
        }
        if speech_model:
            params["speech_model"] = speech_model
        codes = [language] if isinstance(language, str) else list(language or [])
        if codes:
            # Steers the multilingual model to the call's language, or to the deployment's
            # languages while it is still being detected (ISO 639-1; a JSON list, as the LiveKit
            # plugin sends it). Universal-Streaming covers es, en, ca, ar, hi and ur
            params["language_codes"] = json.dumps(codes)
        if language_detection:
            # Only reporting: final turns carry `language_code` and `language_confidence`
            params["language_detection"] = "true"
        self.language = language
        if keyterms:
            # Vocabulario de la finca: sin él AssemblyAI se come las letras sueltas
            # ("puerta A" -> "Puerta.", "tercero A" -> "tercero"). Medido 2026-08-28.
            params["keyterms_prompt"] = json.dumps(keyterms[:100], ensure_ascii=False)
        self.keyterms = list(keyterms or [])
        self._url = f"wss://{host}/v3/ws?" + urlencode(params)
        self._min_bytes = int(sample_rate * min_chunk_ms / 1000) * 2
        self._buffer = bytearray()
        self._ws: Any = None
        self._events: asyncio.Queue[TurnEvent | None] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self.connection_ms: int | None = None
        self.session_id: str | None = None
        self.closing: str | None = None

    async def open(self) -> None:
        t0 = perf_counter()
        self._ws = await websockets.connect(
            self._url,
            additional_headers={"Authorization": self.api_key},
            max_queue=None,
            open_timeout=20,
        )
        primero = json.loads(await self._ws.recv())
        self.connection_ms = int((perf_counter() - t0) * 1000)
        if primero.get("type") == "Error":
            raise AssemblyAIConfigError(f"AssemblyAI rechazó la sesión: {primero.get('error')}")
        self.session_id = primero.get("id")
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                m = json.loads(raw)
                kind = m.get("type")
                if kind == "Turn":
                    if m.get("end_of_turn") and m.get("turn_is_formatted"):
                        await self._events.put(
                            TurnEvent(
                                "final",
                                (m.get("transcript") or "").strip(),
                                detail={
                                    "turn_end_confidence": m.get("end_of_turn_confidence"),
                                    "words": len(m.get("words") or []),
                                    # With language_detection: what AssemblyAI heard
                                    "language_code": m.get("language_code"),
                                    "language_confidence": m.get("language_confidence"),
                                },
                            )
                        )
                    elif not m.get("end_of_turn"):
                        await self._events.put(
                            TurnEvent("partial", (m.get("transcript") or "").strip())
                        )
                elif kind == "Error":
                    await self._events.put(TurnEvent("error", str(m.get("error")), detail=m))
                elif kind == "Termination":
                    break
        except websockets.ConnectionClosed as e:
            self.closing = f"{e.code} {e.reason}"
            if e.code not in (1000, 1001):
                await self._events.put(
                    TurnEvent("error", f"conexión cerrada ({e.code} {e.reason})")
                )
        except Exception as e:  # noqa: BLE001
            self.closing = f"excepción {type(e).__name__}: {e}"
            await self._events.put(TurnEvent("error", self.closing))
        finally:
            await self._events.put(None)

    async def send_audio(self, pcm: bytes) -> None:
        """Agrupa hasta ≥ min_chunk_ms (AssemblyAI exige trozos de 50-1000 ms)."""
        self._buffer.extend(pcm)
        while len(self._buffer) >= self._min_bytes:
            chunk = bytes(self._buffer[: self._min_bytes])
            del self._buffer[: self._min_bytes]
            await self._ws.send(chunk)

    async def set_language(self, language: str) -> None:
        """The call's language was decided (or switched): steer the rest of the session to it,
        without reconnecting (`UpdateConfiguration`, as the LiveKit plugin does)."""
        if self._ws is None or self.language == language:
            return
        self.language = language
        await self._ws.send(
            json.dumps({"type": "UpdateConfiguration", "language_codes": [language]})
        )

    async def force_end_of_turn(self) -> None:
        """Vacía el búfer con relleno de silencio y pide a AssemblyAI cerrar el turno actual."""
        if self._buffer:
            missing = self._min_bytes - len(self._buffer)
            self._buffer.extend(b"\x00" * max(0, missing))
            await self._ws.send(bytes(self._buffer))
            self._buffer.clear()
        await self._ws.send(json.dumps({"type": "ForceEndpoint"}))

    async def events(self) -> AsyncIterator[TurnEvent]:
        while True:
            ev = await self._events.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "Terminate"}))
            await asyncio.wait_for(self._ws.wait_closed(), timeout=3)
        except Exception:  # noqa: BLE001
            await self._ws.close()
        if self._reader:
            self._reader.cancel()
