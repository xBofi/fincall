"""Agente de voz de LiveKit: oído y boca de la llamada; el cerebro sigue siendo la API.

Escucha al vecino (AssemblyAI UE), pasa cada turno a la API (`/api/calls/...`, con
`BC_AGENT_SECRET`), dice lo que devuelve el grafo (ElevenLabs, PCM para que las marcas de
tiempo por palabra sean exactas) e informa de qué palabras llegaron a sonar (ledger de entrega).
No tiene lógica de negocio.

Autoalojado sin llamadas a la nube de LiveKit: el fin de turno lo decide AssemblyAI
(`turn_detection="stt"`), la interrupción es por VAD local (silero) y no se graba la sesión.
Cómo se ordenan y juntan los turnos partidos: `building_call.voice.control`. Medidas de los
ajustes: `docs/superpowers/plans/2026-10-21-delivery-ledger-phase-2.md`.
Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md`.

Uso (desarrollo): `uv run --extra voice building-call-agent dev`. Con `BC_LIVEKIT_AGENT` se
registra con otro nombre (un agente de pruebas junto al de la consola).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterable
from typing import Any

import httpx
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    JobContext,
    ModelSettings,
    UserInputTranscribedEvent,
    cli,
    llm,
    stt,
)
from livekit.plugins import assemblyai, elevenlabs, silero

from building_call.graph.ledger import cut_by_words
from building_call.settings import settings
from building_call.stt_streaming import validate_eu_endpoint
from building_call.voice.control import CallControl

logger = logging.getLogger(__name__)


class ApiClient:
    """Las mismas rutas que usa la consola, con el secreto del agente."""

    def __init__(self, base_url: str, secret: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {secret}"}, timeout=30
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(path, json=body)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def vocabulary(self) -> list[str]:
        r = await self._http.get("/api/voice/vocabulary")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def begin(self, phone: str | None) -> dict[str, Any]:
        return await self._post("/api/calls", {"caller_phone": phone})

    async def get(self, call_id: str) -> dict[str, Any]:
        r = await self._http.get(f"/api/calls/{call_id}")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def turn(self, call_id: str, text: str) -> dict[str, Any]:
        return await self._post(f"/api/calls/{call_id}/messages", {"text": text})

    async def partial(self, call_id: str, text: str) -> None:
        await self._post(f"/api/calls/{call_id}/partial", {"text": text})

    async def delivery(
        self, call_id: str, turn: int, interrupted: bool, delivered_text: str, ratio: float
    ) -> dict[str, Any]:
        state = "interrupted" if interrupted else "completed"
        if interrupted and not delivered_text:
            state = "not_played"
        r = await self._post(
            f"/api/calls/{call_id}/delivery",
            {"turn": turn, "state": state, "ratio": ratio, "delivered_text": delivered_text},
        )
        return r.get("last_delivery") or {}

    async def metrics(self, call_id: str, turn: int, response_ms: int | None) -> None:
        body = {"turn": turn, "response_ms": response_ms, "stt_client": "livekit"}
        try:
            await self._post(f"/api/calls/{call_id}/metrics", body)
        except httpx.HTTPStatusError:
            pass  # turno sin métricas de servidor (el saludo): no rompe nada

    async def close(self) -> None:
        await self._http.aclose()


class _NoModel(llm.LLM[None]):
    """LiveKit no llama a `llm_node` si la sesión no tiene LLM. Este nunca se usa: el texto lo
    pone el grafo a través de la API."""

    def chat(self, **_: Any) -> Any:  # pragma: no cover - nunca se llama
        raise NotImplementedError("el texto lo decide la API, no un LLM de LiveKit")


class FincallAgent(Agent):
    def __init__(self, api: ApiClient, control: CallControl) -> None:
        super().__init__(instructions="", llm=_NoModel())
        self.api = api
        self.control = control

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> AsyncIterable[stt.SpeechEvent]:
        """El STT de siempre; solo anota si el vecino seguía hablando al cerrarse cada turno."""
        async for ev in Agent.default.stt_node(self, audio, model_settings):
            if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                # Aún no ha pasado por la sesión: el estado es el del VAD, no el del cierre
                speaking = self.session.user_state == "speaking"
                logger.debug("AssemblyAI cierra turno; el vecino sigue hablando: %s", speaking)
                self.control.on_turn_closed(speaking)
            yield ev

    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[Any], model_settings: ModelSettings
    ) -> AsyncIterable[str]:
        # Lo que el vecino ha dicho desde el último turno enviado (un turno partido va junto)
        utterance = self.control.to_send(
            m for m in chat_ctx.items if isinstance(m, llm.ChatMessage)
        )
        if not utterance:
            return
        # Pausa a media frase o solo muletillas: si llega el resto, LiveKit cancela esto y el
        # texto va junto con el turno siguiente
        try:
            waited = await self.control.wait_before_sending(
                utterance, lambda: self.session.user_state == "speaking"
            )
        except asyncio.CancelledError:
            logger.debug("Turno retenido y cancelado: irá junto con el siguiente")
            raise
        logger.debug("Turno enviado (retenido antes: %s)", waited)
        try:
            # En orden y sin cortar la petición si LiveKit cancela esta generación
            await self.control.send_turn(self.api, utterance)
        except httpx.HTTPError:
            logger.exception("La API no respondió al turno de %s", self.control.call_id)
            return
        if self.control.text:
            yield self.control.text


#: Los modelos de AssemblyAI que aceptan fijar el idioma (el plugin rechaza el resto)
_MODELS_WITH_LANGUAGE = ("u3-rt-pro", "u3-rt-pro-beta-1", "universal-3-5-pro", "universal-3-6-pro")


def stt_options(vocabulary: list[str]) -> dict[str, Any]:
    """Argumentos de `assemblyai.STT`. Separado para probarlo sin abrir conexiones."""
    host = settings.assemblyai_streaming_host
    validate_eu_endpoint(host)  # solo el endpoint europeo, como el resto del sistema
    model = settings.assemblyai_model
    options: dict[str, Any] = {
        "model": model,
        "api_key": settings.assemblyai_api_key,
        "base_url": f"wss://{host}",
        "format_turns": True,
        "end_of_turn_confidence_threshold": settings.assemblyai_eot_confidence,
        "min_turn_silence": settings.livekit_stt_silence_min_ms,
        "max_turn_silence": settings.livekit_stt_silence_max_ms,
        # Sin el vocabulario de la finca AssemblyAI se come las letras sueltas (puertas)
        "keyterms_prompt": vocabulary[:100],
    }
    # Solo los Universal-3 Pro aceptan fijar el idioma; el multilingüe lo detecta
    if model in _MODELS_WITH_LANGUAGE:
        options["language_codes"] = "es"
    return options


def _stt(vocabulary: list[str]) -> assemblyai.STT:
    return assemblyai.STT(**stt_options(vocabulary))


def vad_options() -> dict[str, Any]:
    """`silero.VAD.load`: corta la voz antes que AssemblyAI el turno (ver `CallControl`)."""
    return {"min_silence_duration": settings.livekit_vad_silence_s}


def stt_context_options() -> dict[str, Any]:
    """`AgentSession.stt_context_options`: sin detección de términos (usaría la nube de LiveKit)
    y, salvo que se active, sin pasar al STT lo que dijo el bot."""
    return {
        "forward_chat_context": settings.livekit_stt_context,
        "keyterm_detection": {"enabled": False},
    }


def turn_options() -> dict[str, Any]:
    """Cuándo acaba el turno del vecino y cuándo corta al bot (`AgentSession.turn_handling`)."""
    return {
        "turn_detection": "stt",
        # AssemblyAI ya espera su silencio de fin de turno: LiveKit solo añade este margen
        "endpointing": {"min_delay": settings.livekit_end_of_turn_min_s},
        "interruption": {
            "mode": "vad",
            "min_duration": settings.livekit_interruption_min_s,
            "min_words": settings.livekit_interruption_min_words,
        },
        # El grafo tiene efectos (crea incidencias): nada de turnos especulativos de LiveKit.
        # La especulación propia va por `/partial` y solo interpreta.
        "preemptive_generation": {"enabled": False},
    }


def _tts() -> elevenlabs.TTS:
    return elevenlabs.TTS(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice,
        model=settings.elevenlabs_model,
        language="es",
        encoding="pcm_24000",  # con v4 las marcas por palabra solo son exactas en PCM
    )


server = AgentServer(
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret,
)


@server.rtc_session(agent_name=settings.livekit_agent)
async def session(ctx: JobContext) -> None:
    api = ApiClient(settings.agent_api_url, settings.agent_secret)
    metadata = json.loads(ctx.job.metadata or "{}")
    # Desde la consola la llamada ya existe; desde el teléfono (fase 3) se crea aquí
    if call_id := metadata.get("call_id"):
        view = await api.get(call_id)
    else:
        view = await api.begin(metadata.get("phone"))
    control = CallControl(view["call_id"])

    session: AgentSession[None] = AgentSession(
        stt=_stt(await api.vocabulary()),
        tts=_tts(),
        vad=silero.VAD.load(**vad_options()),
        use_tts_aligned_transcript=True,
        turn_handling=turn_options(),  # type: ignore[arg-type]
        stt_context_options=stt_context_options(),  # type: ignore[arg-type]
    )

    tasks: set[asyncio.Task[Any]] = set()

    def launch(coroutine: Any) -> asyncio.Task[Any]:
        task: asyncio.Task[Any] = asyncio.ensure_future(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    @session.on("user_input_transcribed")
    def _partial(ev: UserInputTranscribedEvent) -> None:
        if not ev.is_final and ev.transcript:
            launch(api.partial(control.call_id, ev.transcript))

    async def hang_up() -> None:
        await api.close()
        ctx.delete_room()

    async def report(item: Any, turn: int, text: str) -> None:
        heard_text = item.text_content or ""
        cut = cut_by_words(text, heard_text) if item.interrupted else len(text)
        delivery = await api.delivery(
            control.call_id, turn, item.interrupted, text[:cut], cut / max(1, len(text))
        )
        e2e = (item.metrics or {}).get("e2e_latency")
        await api.metrics(control.call_id, turn, int(e2e * 1000) if e2e else None)
        action, cut_pieces = control.on_finished_speaking(delivery)
        if action == "repeat_and_hang_up":
            await session.say(" ".join(cut_pieces), allow_interruptions=False).wait_for_playout()
        if action != "proceed":
            await hang_up()

    @session.on("conversation_item_added")
    def _said(ev: ConversationItemAddedEvent) -> None:
        if getattr(ev.item, "role", None) == "assistant":
            # El turno al que se refiere se toma ya: el siguiente puede estar en camino
            turn, text = control.report()
            control.track_report(launch(report(ev.item, turn, text)))

    await session.start(agent=FincallAgent(api, control), room=ctx.room, record=False)
    # El saludo (o lo último pendiente) ya lo ha preparado la API
    if text := control.new_turn(view):
        session.say(text)


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()
