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


def validar_endpoint_eu(url: str) -> str:
    """Solo se permiten endpoints europeos (decisión de Andreu, 2026-08-28)."""
    if ".eu.assemblyai.com" not in url:
        raise AssemblyAIConfigError(
            f"Endpoint AssemblyAI no europeo rechazado: {url}. Use https://api.eu.assemblyai.com"
        )
    return url.rstrip("/")


@dataclass
class EventoTurno:
    tipo: str  # parcial | final | error
    texto: str = ""
    recibido_en: float = field(default_factory=perf_counter)
    detalle: dict[str, Any] = field(default_factory=dict)


class SesionStreamingAssemblyAI:
    """Una sesión por llamada. `enviar_audio` acepta PCM16 mono; `eventos()` produce turnos."""

    def __init__(
        self,
        api_key: str,
        sample_rate: int = 16000,
        host: str = HOST_STREAMING_EU,
        min_chunk_ms: int = 100,
        eot_confianza: float = 0.7,
        eot_silencio_ms: int = 300,
        max_silencio_ms: int = 800,
        keyterms: list[str] | None = None,
    ) -> None:
        """Fin de turno: AssemblyAI cierra el turno cuando su confianza de que la frase ha
        terminado supera `eot_confianza` y hay `eot_silencio_ms` de silencio; si no está
        seguro, espera `max_silencio_ms`. Los valores por defecto del proveedor (0.7 / 160 ms)
        cortaban al vecino a mitad de frase (prueba de Andreu, 2026-08-28)."""
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
            "end_of_turn_confidence_threshold": eot_confianza,
            "min_end_of_turn_silence_when_confident": eot_silencio_ms,
            "max_turn_silence": max_silencio_ms,
        }
        if keyterms:
            # Vocabulario de la finca: sin él AssemblyAI se come las letras sueltas
            # ("puerta A" -> "Puerta.", "tercero A" -> "tercero"). Medido 2026-08-28.
            params["keyterms_prompt"] = json.dumps(keyterms[:100], ensure_ascii=False)
        self.keyterms = list(keyterms or [])
        self._url = f"wss://{host}/v3/ws?" + urlencode(params)
        self._min_bytes = int(sample_rate * min_chunk_ms / 1000) * 2
        self._buffer = bytearray()
        self._ws: Any = None
        self._eventos: asyncio.Queue[EventoTurno | None] = asyncio.Queue()
        self._lector: asyncio.Task[None] | None = None
        self.conexion_ms: int | None = None
        self.sesion_id: str | None = None
        self.cierre: str | None = None

    async def abrir(self) -> None:
        t0 = perf_counter()
        self._ws = await websockets.connect(
            self._url,
            additional_headers={"Authorization": self.api_key},
            max_queue=None,
            open_timeout=20,
        )
        primero = json.loads(await self._ws.recv())
        self.conexion_ms = int((perf_counter() - t0) * 1000)
        if primero.get("type") == "Error":
            raise AssemblyAIConfigError(f"AssemblyAI rechazó la sesión: {primero.get('error')}")
        self.sesion_id = primero.get("id")
        self._lector = asyncio.create_task(self._leer())

    async def _leer(self) -> None:
        try:
            async for crudo in self._ws:
                if isinstance(crudo, bytes):
                    continue
                m = json.loads(crudo)
                tipo = m.get("type")
                if tipo == "Turn":
                    if m.get("end_of_turn") and m.get("turn_is_formatted"):
                        await self._eventos.put(
                            EventoTurno(
                                "final",
                                (m.get("transcript") or "").strip(),
                                detalle={
                                    "confianza_fin_turno": m.get("end_of_turn_confidence"),
                                    "palabras": len(m.get("words") or []),
                                },
                            )
                        )
                    elif not m.get("end_of_turn"):
                        await self._eventos.put(
                            EventoTurno("parcial", (m.get("transcript") or "").strip())
                        )
                elif tipo == "Error":
                    await self._eventos.put(EventoTurno("error", str(m.get("error")), detalle=m))
                elif tipo == "Termination":
                    break
        except websockets.ConnectionClosed as e:
            self.cierre = f"{e.code} {e.reason}"
            if e.code not in (1000, 1001):
                await self._eventos.put(
                    EventoTurno("error", f"conexión cerrada ({e.code} {e.reason})")
                )
        except Exception as e:  # noqa: BLE001
            self.cierre = f"excepción {type(e).__name__}: {e}"
            await self._eventos.put(EventoTurno("error", self.cierre))
        finally:
            await self._eventos.put(None)

    async def enviar_audio(self, pcm: bytes) -> None:
        """Agrupa hasta ≥ min_chunk_ms (AssemblyAI exige trozos de 50-1000 ms)."""
        self._buffer.extend(pcm)
        while len(self._buffer) >= self._min_bytes:
            trozo = bytes(self._buffer[: self._min_bytes])
            del self._buffer[: self._min_bytes]
            await self._ws.send(trozo)

    async def forzar_fin_turno(self) -> None:
        """Vacía el búfer con relleno de silencio y pide a AssemblyAI cerrar el turno actual."""
        if self._buffer:
            faltan = self._min_bytes - len(self._buffer)
            self._buffer.extend(b"\x00" * max(0, faltan))
            await self._ws.send(bytes(self._buffer))
            self._buffer.clear()
        await self._ws.send(json.dumps({"type": "ForceEndpoint"}))

    async def eventos(self) -> AsyncIterator[EventoTurno]:
        while True:
            ev = await self._eventos.get()
            if ev is None:
                return
            yield ev

    async def cerrar(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "Terminate"}))
            await asyncio.wait_for(self._ws.wait_closed(), timeout=3)
        except Exception:  # noqa: BLE001
            await self._ws.close()
        if self._lector:
            self._lector.cancel()
