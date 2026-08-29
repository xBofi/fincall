"""Puente entre una llamada telefónica y el grafo, independiente del proveedor.

El adaptador del proveedor (Twilio u otro) implementa `CanalTelefono` y llama a:
- `iniciar()` al empezar la llamada (crea el expediente, abre el STT y reproduce el saludo),
- `recibir_ulaw(bytes)` con cada trozo de audio del vecino,
- `marca_reproducida(nombre)` cuando el proveedor confirma que un turno terminó de sonar,
- `cerrar()` al colgar.

El puente se encarga de STT en streaming (misma sesión AssemblyAI que la consola web), del grafo
(`GestorLlamadas`), del TTS de servidor, del *barge-in* (si el vecino habla ≥2 palabras mientras
suena el bot, se corta la reproducción), del ledger de entrega, de las métricas de latencia
(`respuesta_ms` = fin de voz del vecino → primer audio del bot) y de guardar la grabación.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol

from building_call.telefonia.audio import EntradaTelefono, SalidaTelefono, energia

logger = logging.getLogger(__name__)

UMBRAL_VOZ = 300  # RMS PCM16; igual que la consola web


class CanalTelefono(Protocol):
    """Lo que el puente necesita del proveedor."""

    async def enviar_audio(self, ulaw: bytes) -> None: ...

    async def marcar(self, nombre: str) -> None: ...

    async def limpiar(self) -> None: ...

    async def colgar(self) -> None: ...


class PuenteLlamada:
    def __init__(
        self,
        gestor: Any,
        fabrica_streaming: Callable[[int, list[str]], Any],
        tts: Any,
        canal: CanalTelefono,
        *,
        vocabulario: list[str],
        telefono: str | None,
        repo: Any = None,
        almacen_audio: Any = None,
        palabras_bargein: int = 2,
    ) -> None:
        self.gestor = gestor
        self.fabrica_streaming = fabrica_streaming
        self.tts = tts
        self.canal = canal
        self.vocabulario = vocabulario
        self.telefono = telefono
        self.repo = repo
        self.almacen_audio = almacen_audio
        self.palabras_bargein = palabras_bargein

        self.llamada_id: str | None = None
        self.turno_sonando: int | None = None
        self.finalizada = False
        self._sesion: Any = None
        self._tarea_eventos: asyncio.Task[None] | None = None
        self._entrada = EntradaTelefono()
        self._grabacion = bytearray()  # vecino, PCM16 16 kHz
        self._grabacion_bot = bytearray()  # asistente, PCM16 a la tasa del TTS
        self._ultima_voz_at: float | None = None
        self._cerrado = False

    # ------------------------------------------------------------------ ciclo de vida

    async def iniciar(self) -> str:
        estado = await self.gestor.iniciar(self.telefono)
        self.llamada_id = estado["llamada_id"]
        self._sesion = self.fabrica_streaming(16000, self.vocabulario)
        await self._sesion.abrir()
        self._tarea_eventos = asyncio.create_task(self._bombear_eventos(), name="telefonia-eventos")
        await self._decir(estado)
        return str(self.llamada_id)

    async def cerrar(self) -> None:
        if self._cerrado:
            return
        self._cerrado = True
        if self._tarea_eventos:
            self._tarea_eventos.cancel()
        if self._sesion is not None:
            try:
                await self._sesion.cerrar()
            except Exception:  # noqa: BLE001
                logger.exception("Error cerrando el STT de la llamada %s", self.llamada_id)
        if self.almacen_audio is not None and self.repo is not None and self.llamada_id:
            try:
                await self.almacen_audio.guardar_pcm(
                    self.repo, self.llamada_id, "cliente", bytes(self._grabacion), 16000
                )
                if self._grabacion_bot:
                    await self.almacen_audio.guardar_pcm(
                        self.repo,
                        self.llamada_id,
                        "asistente",
                        bytes(self._grabacion_bot),
                        int(self.tts.tasa),
                    )
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo guardar el audio de la llamada %s", self.llamada_id)

    # ------------------------------------------------------------------ entrada

    async def recibir_ulaw(self, ulaw: bytes) -> None:
        if self._sesion is None or self._cerrado:
            return
        pcm = self._entrada.convertir(ulaw)
        if energia(pcm) > UMBRAL_VOZ:
            self._ultima_voz_at = perf_counter()
        self._grabacion.extend(pcm)
        await self._sesion.enviar_audio(pcm)

    async def marca_reproducida(self, nombre: str) -> None:
        """El proveedor confirma que el audio hasta la marca ya sonó en el teléfono."""
        if not nombre.startswith("turno-") or self.llamada_id is None:
            return
        turno = int(nombre.split("-", 1)[1])
        if self.turno_sonando == turno:
            self.turno_sonando = None
            self.gestor.registrar_entrega(self.llamada_id, turno, "completada", 1.0, None)
        if self.finalizada:
            await self.canal.colgar()

    # ------------------------------------------------------------------ salida

    async def _interrumpir(self) -> None:
        """Barge-in: el vecino habla mientras suena el bot. Se corta y se anota la entrega parcial."""
        if self.turno_sonando is None or self.llamada_id is None:
            return
        turno, self.turno_sonando = self.turno_sonando, None
        await self.canal.limpiar()
        self.gestor.registrar_entrega(self.llamada_id, turno, "interrumpida", 0.5, None)

    async def _decir(self, estado: dict[str, Any]) -> None:
        texto = " ".join(estado.get("mensajes_bot") or []).strip()
        turno = int(estado.get("turno", 0))
        self.finalizada = bool(estado.get("finalizada"))
        if not texto:
            if self.finalizada:
                await self.canal.colgar()
            return
        t0 = perf_counter()
        try:
            pcm = await self.tts.sintetizar(texto)
        except Exception:  # noqa: BLE001
            logger.exception("TTS falló en la llamada %s; se corta la llamada", self.llamada_id)
            await self.canal.colgar()
            return
        self._grabacion_bot.extend(pcm)
        salida = SalidaTelefono(self.tts.tasa)
        primero_at: float | None = None
        for trozo in salida.convertir(pcm, final=True):
            if primero_at is None:
                primero_at = perf_counter()
            await self.canal.enviar_audio(trozo)
        self.turno_sonando = turno
        await self.canal.marcar(f"turno-{turno}")
        if primero_at is not None and turno > 0 and self.llamada_id:
            metricas = getattr(self.gestor, "metricas", None)
            if metricas is not None:
                metricas.completar_cliente(
                    self.llamada_id,
                    turno,
                    {
                        "tts_inicio_ms": int((primero_at - t0) * 1000),
                        "respuesta_ms": int((primero_at - self._ultima_voz_at) * 1000)
                        if self._ultima_voz_at
                        else None,
                        "stt_cliente": "telefono",
                        "extra": {"canal": "telefono"},
                    },
                )

    async def _bombear_eventos(self) -> None:
        assert self._sesion is not None
        async for ev in self._sesion.eventos():
            if ev.tipo == "parcial":
                if (
                    self.turno_sonando is not None
                    and len(ev.texto.split()) >= self.palabras_bargein
                ):
                    await self._interrumpir()
            elif ev.tipo == "error":
                logger.warning("STT en la llamada %s: %s", self.llamada_id, ev.texto)
            elif ev.tipo == "final" and ev.texto:
                if self.turno_sonando is not None:
                    await self._interrumpir()
                if self.finalizada:
                    continue
                estado = await self.gestor.mensaje(self.llamada_id, ev.texto)
                await self._decir(estado)
