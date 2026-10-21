"""Agente de voz de LiveKit: oído y boca de la llamada; el cerebro sigue siendo la API.

Escucha al vecino (AssemblyAI UE), pasa cada turno a la API (`/api/llamadas/...`, con
`BC_AGENTE_SECRETO`), dice lo que devuelve el grafo (ElevenLabs, PCM para que las marcas de
tiempo por palabra sean exactas) e informa de qué palabras llegaron a sonar (ledger de entrega).
No tiene lógica de negocio.

Autoalojado sin llamadas a la nube de LiveKit: el fin de turno lo decide AssemblyAI
(`turn_detection="stt"`), la interrupción es por VAD local (silero) y no se graba la sesión.
Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md`.

Uso (desarrollo): `uv run --extra voz building-call-agente dev`
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterable
from typing import Any

import httpx
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
)
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import assemblyai, elevenlabs, silero

from building_call.graph.ledger import corte_por_palabras
from building_call.livekit_api import NOMBRE_AGENTE
from building_call.settings import settings
from building_call.stt_streaming import validar_endpoint_eu
from building_call.voz.control import ControlLlamada

logger = logging.getLogger(__name__)


class ClienteApi:
    """Las mismas rutas que usa la consola, con el secreto del agente."""

    def __init__(self, base_url: str, secreto: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {secreto}"}, timeout=30
        )

    async def _post(self, ruta: str, cuerpo: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(ruta, json=cuerpo)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def vocabulario(self) -> list[str]:
        r = await self._http.get("/api/voz/vocabulario")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def iniciar(self, telefono: str | None) -> dict[str, Any]:
        return await self._post("/api/llamadas", {"telefono_llamante": telefono})

    async def obtener(self, llamada_id: str) -> dict[str, Any]:
        r = await self._http.get(f"/api/llamadas/{llamada_id}")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    async def turno(self, llamada_id: str, texto: str) -> dict[str, Any]:
        return await self._post(f"/api/llamadas/{llamada_id}/mensajes", {"texto": texto})

    async def parcial(self, llamada_id: str, texto: str) -> None:
        await self._post(f"/api/llamadas/{llamada_id}/parcial", {"texto": texto})

    async def entrega(
        self, llamada_id: str, turno: int, interrumpida: bool, texto_entregado: str, ratio: float
    ) -> dict[str, Any]:
        estado = "interrumpida" if interrumpida else "completada"
        if interrumpida and not texto_entregado:
            estado = "no_reproducida"
        r = await self._post(
            f"/api/llamadas/{llamada_id}/entrega",
            {"turno": turno, "estado": estado, "ratio": ratio, "texto_entregado": texto_entregado},
        )
        return r.get("entrega_ultima") or {}

    async def metricas(self, llamada_id: str, turno: int, respuesta_ms: int | None) -> None:
        cuerpo = {"turno": turno, "respuesta_ms": respuesta_ms, "stt_cliente": "livekit"}
        try:
            await self._post(f"/api/llamadas/{llamada_id}/metricas", cuerpo)
        except httpx.HTTPStatusError:
            pass  # turno sin métricas de servidor (el saludo): no rompe nada

    async def cerrar(self) -> None:
        await self._http.aclose()


class _SinModelo(llm.LLM[None]):
    """LiveKit no llama a `llm_node` si la sesión no tiene LLM. Este nunca se usa: el texto lo
    pone el grafo a través de la API."""

    def chat(self, **_: Any) -> Any:  # pragma: no cover - nunca se llama
        raise NotImplementedError("el texto lo decide la API, no un LLM de LiveKit")


class AgenteFincall(Agent):
    def __init__(self, api: ClienteApi, control: ControlLlamada) -> None:
        super().__init__(instructions="", llm=_SinModelo())
        self.api = api
        self.control = control

    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[Any], model_settings: ModelSettings
    ) -> AsyncIterable[str]:
        entrada = next(
            (
                m.text_content
                for m in reversed(chat_ctx.items)
                if isinstance(m, llm.ChatMessage) and m.role == "user" and m.text_content
            ),
            "",
        )
        try:
            vista = await self.api.turno(self.control.llamada_id, entrada or "")
        except httpx.HTTPError:
            logger.exception("La API no respondió al turno de %s", self.control.llamada_id)
            return
        texto = self.control.nuevo_turno(vista)
        if texto:
            yield texto


#: Los modelos de AssemblyAI que aceptan fijar el idioma (el plugin rechaza el resto)
_MODELOS_CON_IDIOMA = ("u3-rt-pro", "u3-rt-pro-beta-1", "universal-3-5-pro", "universal-3-6-pro")


def _stt(vocabulario: list[str]) -> assemblyai.STT:
    host = settings.assemblyai_streaming_host
    validar_endpoint_eu(host)  # solo el endpoint europeo, como el resto del sistema
    return assemblyai.STT(
        model=settings.assemblyai_modelo,  # type: ignore[arg-type]
        api_key=settings.assemblyai_api_key,
        base_url=f"wss://{host}",
        # Solo los Universal-3 Pro aceptan fijar el idioma; el multilingüe lo detecta
        language_codes="es" if settings.assemblyai_modelo in _MODELOS_CON_IDIOMA else NOT_GIVEN,
        format_turns=True,
        end_of_turn_confidence_threshold=settings.assemblyai_eot_confianza,
        min_turn_silence=settings.assemblyai_eot_silencio_ms,
        max_turn_silence=settings.assemblyai_max_silencio_ms,
        # Sin el vocabulario de la finca AssemblyAI se come las letras sueltas (puertas)
        keyterms_prompt=vocabulario[:100],
    )


def _tts() -> elevenlabs.TTS:
    return elevenlabs.TTS(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voz,
        model=settings.elevenlabs_modelo,
        language="es",
        encoding="pcm_24000",  # con v4 las marcas por palabra solo son exactas en PCM
    )


servidor = AgentServer(
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret,
)


@servidor.rtc_session(agent_name=NOMBRE_AGENTE)
async def sesion(ctx: JobContext) -> None:
    api = ClienteApi(settings.agente_api_url, settings.agente_secreto)
    metadata = json.loads(ctx.job.metadata or "{}")
    # Desde la consola la llamada ya existe; desde el teléfono (fase 3) se crea aquí
    if llamada_id := metadata.get("llamada_id"):
        vista = await api.obtener(llamada_id)
    else:
        vista = await api.iniciar(metadata.get("telefono"))
    control = ControlLlamada(vista["llamada_id"])

    session: AgentSession[None] = AgentSession(
        stt=_stt(await api.vocabulario()),
        tts=_tts(),
        vad=silero.VAD.load(),
        use_tts_aligned_transcript=True,
        turn_handling={
            "turn_detection": "stt",
            # AssemblyAI ya espera su silencio de fin de turno: sin espera extra de LiveKit
            "endpointing": {"min_delay": 0.0},
            "interruption": {"mode": "vad"},
            # El grafo tiene efectos (crea incidencias): nada de turnos especulativos de LiveKit.
            # La especulación propia va por `/parcial` y solo interpreta.
            "preemptive_generation": {"enabled": False},
        },
    )

    tareas: set[asyncio.Task[Any]] = set()

    def lanzar(corrutina: Any) -> None:
        tarea = asyncio.ensure_future(corrutina)
        tareas.add(tarea)
        tarea.add_done_callback(tareas.discard)

    @session.on("user_input_transcribed")
    def _parcial(ev: UserInputTranscribedEvent) -> None:
        if not ev.is_final and ev.transcript:
            lanzar(api.parcial(control.llamada_id, ev.transcript))

    async def colgar() -> None:
        await api.cerrar()
        ctx.delete_room()

    async def informar(item: Any) -> None:
        turno, texto = control.turno, control.texto
        oido = item.text_content or ""
        corte = corte_por_palabras(texto, oido) if item.interrupted else len(texto)
        entrega = await api.entrega(
            control.llamada_id, turno, item.interrupted, texto[:corte], corte / max(1, len(texto))
        )
        e2e = (item.metrics or {}).get("e2e_latency")
        await api.metricas(control.llamada_id, turno, int(e2e * 1000) if e2e else None)
        accion, cortados = control.al_terminar_de_hablar(entrega)
        if accion == "repetir_y_colgar":
            await session.say(" ".join(cortados), allow_interruptions=False).wait_for_playout()
        if accion != "seguir":
            await colgar()

    @session.on("conversation_item_added")
    def _dicho(ev: ConversationItemAddedEvent) -> None:
        if getattr(ev.item, "role", None) == "assistant":
            lanzar(informar(ev.item))

    await session.start(agent=AgenteFincall(api, control), room=ctx.room, record=False)
    # El saludo (o lo último pendiente) ya lo ha preparado la API
    if texto := control.nuevo_turno(vista):
        session.say(texto)


def main() -> None:
    cli.run_app(servidor)


if __name__ == "__main__":
    main()
