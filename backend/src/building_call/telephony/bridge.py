"""Puente entre una llamada telefónica y el grafo, independiente del proveedor.

El adaptador del proveedor (Twilio u otro) implementa `PhoneChannel` y llama a:
- `iniciar()` al empezar la llamada (crea el expediente, abre el STT y reproduce el saludo),
- `recibir_ulaw(bytes)` con cada trozo de audio del vecino,
- `marca_reproducida(nombre)` cuando el proveedor confirma que un turno terminó de sonar,
- `cerrar()` al colgar.

El puente se encarga de STT en streaming (misma sesión AssemblyAI que la consola web), del grafo
(`CallManager`), del TTS de servidor, del *barge-in* (si el vecino habla ≥2 palabras mientras
suena el bot, se corta la reproducción), del ledger de entrega, de las métricas de latencia
(`response_ms` = fin de voz del vecino → primer audio del bot) y de la grabación alineada de las
dos voces (`recording.ConversationRecorder`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol

from building_call import i18n
from building_call.recording import ConversationRecorder
from building_call.telephony.audio import PhoneInput, PhoneOutput, energy
from building_call.telephony.tts import fragments, voice_for

logger = logging.getLogger(__name__)

VOICE_THRESHOLD = 300  # RMS PCM16; igual que la consola web

# Si un turno falla (LLM caído o lento, error del grafo) el vecino no puede quedarse en silencio:
# se le pide que repita y, si vuelve a fallar, se despide y la llamada queda para revisar.
TURN_LIMIT_S = 10.0
MAX_CONSECUTIVE_FAILURES = 2
# What the caller hears when a turn fails is in the call's language: `Phrases.turn_failed` and
# `Phrases.technical_failure` in `i18n/locales/<language>.toml`


class PhoneChannel(Protocol):
    """Lo que el puente necesita del proveedor."""

    async def send_audio(self, ulaw: bytes) -> None: ...

    async def mark(self, name: str) -> None: ...

    async def clean_up(self) -> None: ...

    async def hang_up(self) -> None: ...


class CallBridge:
    def __init__(
        self,
        manager: Any,
        streaming_factory: Callable[[int, list[str], str | list[str]], Any],
        tts: Any,
        channel: PhoneChannel,
        *,
        vocabulary: list[str],
        phone: str | None,
        repo: Any = None,
        audio_store: Any = None,
        bargein_words: int = 2,
        turn_limit_s: float = TURN_LIMIT_S,
        existing_call_id: str | None = None,
        language: str | None = None,
        persona: str | None = None,
    ) -> None:
        """`existing_call_id`: the console opens the call (and shows it live) before the
        browser dials Twilio. `language`: chosen for this call (the number, a stream
        parameter); None = `BC_LANGUAGE`."""
        self.manager = manager
        self.streaming_factory = streaming_factory
        self.tts = tts
        self.channel = channel
        self.vocabulary = vocabulary
        self.phone = phone
        self.repo = repo
        self.audio_store = audio_store
        self.bargein_words = bargein_words
        self.turn_limit_s = turn_limit_s
        self._consecutive_failures = 0

        self.call_id: str | None = None
        self.existing_call_id = existing_call_id
        self.requested_language = language
        self.persona = persona
        self.language = i18n.get(language).code if language else i18n.default_code()
        self.playing_turn: int | None = None
        self.finished = False
        self._session: Any = None
        self._events_task: asyncio.Task[None] | None = None
        self._utterance = PhoneInput()
        self._recorder = ConversationRecorder()  # las dos voces sobre el mismo reloj
        self._last_voice_at: float | None = None
        self._closed = False

    # ------------------------------------------------------------------ ciclo de vida

    async def begin(self) -> str:
        if self.existing_call_id:
            state = await self.manager.get(self.existing_call_id)
        else:
            state = await self.manager.begin(
                self.phone, self.requested_language, self.persona
            )
        self.call_id = state["call_id"]
        self.language = str(state.get("language") or self.language)
        self.persona = state.get("persona")
        # A call whose language nobody chose listens to every enabled language until the
        # caller's first words decide it (`CallManager` switches, `_follow_language` steers)
        language: str | list[str] = (
            self.language if state.get("language_locked") else list(i18n.enabled())
        )
        self._session = self.streaming_factory(16000, self.vocabulary, language)
        await self._session.open()
        self._events_task = asyncio.create_task(self._pump_events(), name="telefonia-eventos")
        await self._say(state)
        return str(self.call_id)

    def _phrase(self, name: str) -> str:
        return str(getattr(i18n.get(self.language).phrases, name))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._events_task:
            self._events_task.cancel()
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                logger.exception("Error cerrando el STT de la llamada %s", self.call_id)
        if self.audio_store is not None and self.repo is not None and self.call_id:
            try:
                await self.audio_store.save_conversation(
                    self.repo, self.call_id, self._recorder
                )
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo guardar el audio de la llamada %s", self.call_id)

    # ------------------------------------------------------------------ entrada

    async def receive_ulaw(self, ulaw: bytes) -> None:
        if self._session is None or self._closed:
            return
        pcm = self._utterance.convert(ulaw)
        if energy(pcm) > VOICE_THRESHOLD:
            self._last_voice_at = perf_counter()
        self._recorder.write_client(pcm)
        await self._session.send_audio(pcm)

    async def mark_played(self, name: str) -> None:
        """El proveedor confirma que el audio hasta la marca ya sonó en el teléfono."""
        if not name.startswith("turno-") or self.call_id is None:
            return
        turn = int(name.split("-", 1)[1])
        if self.playing_turn == turn:
            self.playing_turn = None
            await self.manager.record_delivery(self.call_id, turn, "completed", 1.0, None)
        if self.finished:
            await self.channel.hang_up()

    # ------------------------------------------------------------------ salida

    async def _interrupt(self) -> None:
        """Barge-in: el vecino habla mientras suena el bot. Se corta y se anota la entrega parcial."""
        if self.playing_turn is None or self.call_id is None:
            return
        turn, self.playing_turn = self.playing_turn, None
        await self.channel.clean_up()
        await self.manager.record_delivery(self.call_id, turn, "interrupted", 0.5, None)

    async def _say(self, state: dict[str, Any]) -> None:
        text = " ".join(state.get("bot_messages") or []).strip()
        turn = int(state.get("turn", 0))
        self.finished = bool(state.get("finished"))
        if not text:
            if self.finished:
                await self.channel.hang_up()
            return
        t0 = perf_counter()
        output = PhoneOutput(self.tts.rate)
        first_at: float | None = None
        pcm = bytearray()
        try:
            # Se envía cada trozo según llega del TTS: el vecino oye el inicio sin esperar la frase
            voice = voice_for(self.persona, self.language)
            async for part in fragments(self.tts, text, self.language, voice):
                pcm += part
                for chunk in output.convert(part):
                    if first_at is None:
                        first_at = perf_counter()
                    await self.channel.send_audio(chunk)
        except Exception:  # noqa: BLE001
            logger.exception("TTS falló en la llamada %s; se corta la llamada", self.call_id)
            await self.channel.hang_up()
            return
        for chunk in output.convert(b"", final=True):
            if first_at is None:
                first_at = perf_counter()
            await self.channel.send_audio(chunk)
        self._recorder.write_assistant(bytes(pcm), int(self.tts.rate))
        self.playing_turn = turn
        await self.channel.mark(f"turno-{turn}")
        if first_at is not None and turn > 0 and self.call_id:
            metrics = getattr(self.manager, "metrics", None)
            if metrics is not None:
                metrics.complete_client(
                    self.call_id,
                    turn,
                    {
                        "tts_start_ms": int((first_at - t0) * 1000),
                        "response_ms": int((first_at - self._last_voice_at) * 1000)
                        if self._last_voice_at
                        else None,
                        "stt_client": "phone",
                        "extra": {"channel": "phone"},
                    },
                )

    async def _follow_language(self, state: dict[str, Any]) -> None:
        """The call switched language: the STT and the voice follow it from the next turn."""
        language = state.get("language")
        if not language or language == self.language:
            return
        self.language = str(language)
        set_language = getattr(self._session, "set_language", None)
        if set_language is not None:
            try:
                await set_language(self.language)
            except Exception:  # noqa: BLE001 - the session keeps every language steered
                logger.warning("No se pudo fijar el idioma %s en el STT", self.language)

    async def _turn(self, text: str, detail: dict[str, Any] | None = None) -> None:
        assert self.call_id is not None
        detail = detail or {}
        try:
            state = await asyncio.wait_for(
                self.manager.message(
                    self.call_id,
                    text,
                    stt_language=detail.get("language_code"),
                    stt_confidence=detail.get("language_confidence"),
                ),
                self.turn_limit_s,
            )
        except Exception:  # noqa: BLE001 - cualquier fallo del turno se le explica al vecino
            self._consecutive_failures += 1
            logger.exception(
                "Turno fallido en la llamada %s (%d seguidos)",
                self.call_id,
                self._consecutive_failures,
            )
            if self._consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                current = await self.manager.get(self.call_id)
                await self._say(
                    {"bot_messages": [self._phrase("turn_failed")], "turn": current.get("turn", 0)}
                )
            else:
                await self._farewell_on_failure("el asistente falló varias veces seguidas")
            return
        self._consecutive_failures = 0
        await self._follow_language(state)
        await self._say(state)

    async def _farewell_on_failure(self, reason: str) -> None:
        assert self.call_id is not None
        state = await self.manager.close_on_failure(self.call_id, reason)
        await self._say({**state, "bot_messages": [self._phrase("technical_failure")]})

    async def _pump_events(self) -> None:
        try:
            await self._listen()
            if not self._closed and not self.finished:
                # El STT cortó el stream sin que nadie colgara: el vecino ya no se oiría
                logger.error("El STT se cerró en mitad de la llamada %s", self.call_id)
                await self._farewell_on_failure("se perdió el reconocimiento de voz")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Fallo en la llamada %s; se cuelga", self.call_id)
            await self.channel.hang_up()

    async def _listen(self) -> None:
        assert self._session is not None
        async for ev in self._session.events():
            if ev.kind == "partial":
                if (
                    self.playing_turn is not None
                    and len(ev.text.split()) >= self.bargein_words
                ):
                    await self._interrupt()
            elif ev.kind == "error":
                logger.warning("STT en la llamada %s: %s", self.call_id, ev.text)
            elif ev.kind == "final" and ev.text:
                if self.playing_turn is not None:
                    await self._interrupt()
                if self.finished:
                    continue
                await self._turn(ev.text, ev.detail)
