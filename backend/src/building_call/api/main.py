"""API HTTP/WebSocket consumida por la consola de validación (y, más adelante, por la telefonía)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from building_call import i18n
from building_call.access import Access
from building_call.audio_store import AudioStore
from building_call.audit.access_log import AccessLog, MemoryAccessStore, PostgresAccessStore
from building_call.audit.api import create_audit_router
from building_call.audit.context import AuditContext, audit_context, set_actor
from building_call.audit.http import AuditMiddleware
from building_call.audit.retention import purge_audit
from building_call.catalog_api import create_catalog_router
from building_call.flow_api import create_flow_router
from building_call.graph import templates
from building_call.graph.ledger import heard_text_if_cut
from building_call.graph.nodes import (
    Dependencies,
    catalog_for,
    equipment_for,
    known_context,
    speculation_key,
)
from building_call.graph.registry import REGISTRY
from building_call.graph.state import CallState
from building_call.i18n import personas
from building_call.inbox_api import create_inbox_router
from building_call.livekit_api import (
    create_livekit_router,
    is_agent,
    livekit_configured,
    requires_user_or_agent,
)
from building_call.llm.interpretation import interpret
from building_call.llm.ollama import OllamaModel
from building_call.llm.race import RaceModel
from building_call.llm.vertex import VertexModel
from building_call.llm.workers_ai import WorkersAIModel
from building_call.metrics import THRESHOLDS_MS
from building_call.oidc import OidcClient
from building_call.operations_api import create_operations_router
from building_call.recording import ConversationRecorder
from building_call.repos.memory import MemoryRepository
from building_call.repos.sql_models import IncidentSQL
from building_call.services import CallManager, CallNotFound
from building_call.settings import settings
from building_call.stt import UnavailableRecognizer, VoiceRecognizer, WhisperRecognizer
from building_call.stt_streaming import AssemblyAIStreamingSession, validate_eu_endpoint
from building_call.telephony.bridge import CallBridge
from building_call.telephony.browser import (
    browser_ready_reason,
    console_identity,
    voice_access_token,
)
from building_call.telephony.configure import not_ready_reason
from building_call.telephony.tts import create_tts, fragments
from building_call.telephony.twilio import TwilioChannel, create_twilio_router

logger = logging.getLogger(__name__)


def _has_voice(pcm: bytes, threshold: int = 300) -> bool:
    """RMS de PCM16 por encima del umbral: sirve para medir el fin de voz en el servidor."""
    import audioop

    try:
        return audioop.rms(pcm, 2) > threshold
    except audioop.error:
        return False


# Fábrica de sesiones de streaming: (sample_rate, keyterms, language) -> sesión con
# abrir/enviar_audio/eventos/cerrar
StreamingFactory = Callable[[int, list[str], str | list[str]], Any]


async def stt_vocabulary(repo: Any, language: str | None = None) -> list[str]:
    """Términos que el STT debe reconocer bien, en el idioma de la llamada: direcciones,
    portales, pisos y puertas reales ("portal 2", "tercero B", "apartment 3A")."""
    estates = await repo.list_estates()
    doors: set[str] = set()
    floors: set[str] = set()
    entrances: set[str] = set()
    for f in estates:
        for v in await repo.homes_of(f.id):
            doors.add(v.door)
            floors.add(v.floor)
            if v.entrance:
                entrances.add(v.entrance)
    return i18n.parser_for(language or i18n.default_code()).stt_terms(
        [f.address for f in estates], sorted(entrances), sorted(floors), sorted(doors)
    )


class NewCall(BaseModel):
    caller_phone: str | None = None
    # ISO 639-1 (`GET /api/languages`); None = BC_LANGUAGE (and detection may switch it)
    language: str | None = Field(default=None, max_length=16)
    # Client voice persona (`GET /api/personas`); None = BC_PERSONA
    persona: str | None = Field(default=None, max_length=64)


class Message(BaseModel):
    # Un turno hablado cabe de sobra; sin tope, /parcial lanzaba el LLM con cientos de KB
    text: str = Field(max_length=2000)


#: Audio de un turno por lotes: sin tope, un fichero de varios GB se cargaba entero en memoria
MAX_AUDIO_BYTES = 10 * 1024 * 1024


class BotVoice(BaseModel):
    text: str = Field(min_length=1, max_length=800)  # cada carácter se paga en una voz por API
    language: str | None = Field(default=None, max_length=16)


class Delivery(BaseModel):
    """Ledger de salida del navegador: qué parte del mensaje del turno llegó a sonar."""

    turn: int
    state: str  # completada | interrumpida | no_reproducida
    ratio: float = 1.0
    delivered_text: str | None = None


class ClientMetrics(BaseModel):
    turn: int
    vad_silence_ms: int | None = None
    network_ms: int | None = None
    tts_start_ms: int | None = None
    response_ms: int | None = None
    stt_client: str | None = None
    extra: dict[str, Any] = {}


def _view(state: CallState) -> dict[str, Any]:
    fields = [
        "estate_id", "estate_address", "home_id", "home_label", "home_confirmed",
        "request", "category", "urgency", "declared_urgency", "summary", "confirmed",
        "specialist_id", "specialist_name", "notice_channel", "notice_target", "incident_id",
        "result", "review_reason", "config_id", "graph", "estate_attempts", "home_attempts",
        "work_type", "equipment_id", "suggestion", "objection",
        "emergency_handled",
    ]  # fmt: skip
    return {
        "call_id": state["call_id"],
        "language": state.get("language"),
        "language_locked": state.get("language_locked", False),
        "persona": state.get("persona"),
        "turn": state.get("turn", 0),
        "current_node": state.get("current_node"),
        "awaiting_user": state.get("awaiting_user", False),
        "finished": state.get("finished", False),
        "bot_messages": state.get("bot_messages", []),
        "history": state.get("history", []),
        "trace": state.get("trace", []),
        "validations": state.get("validations", []),
        "confidence_signals": state.get("confidence_signals"),
        "interpretation": state.get("interpretation"),
        "pending": state.get("pending", {}),
        "last_delivery": state.get("last_delivery"),
        "pieces": state.get("bot_pieces", []),
        "timings": state.get("turn_timings", {}),
        "config": state.get("config"),
        "data": {c: state.get(c) for c in fields},
    }


def create_llm() -> Any:
    """Selecciona el proveedor LLM según BC_LLM."""
    local = OllamaModel(
        settings.ollama_url,
        settings.ollama_model,
        settings.ollama_timeout_s,
        num_predict=settings.ollama_num_predict,
        num_ctx=settings.ollama_num_ctx,
    )
    mode = settings.llm
    has_cloudflare = bool(settings.cloudflare_account_id and settings.cloudflare_api_token)
    if mode == "auto":
        mode = "race" if has_cloudflare else "ollama"
    if mode == "ollama":
        return local
    if mode == "vertex":
        return VertexModel(
            settings.vertex_project,
            settings.vertex_model,
            location=settings.vertex_location,
            credentials_path=settings.vertex_credentials,
            timeout_s=settings.vertex_timeout_s,
        )
    cloud = WorkersAIModel(
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_model,
        timeout_s=settings.cloudflare_timeout_s,
    )
    if mode == "workers_ai":
        return cloud
    if mode == "race":
        return RaceModel(
            cloud, local, preferred=0, extra_wait_ms=settings.race_extra_wait_ms
        )
    raise ValueError(f"BC_LLM desconocido: {settings.llm}")


def create_phraser() -> OllamaModel | None:
    """Redacción con el modelo local y salida corta (la carrera con el 27B tardaba 1,5 s).

    Solo cuando Ollama ya forma parte del despliegue: con un proveedor por API (Vertex, Workers
    AI) no hay Ollama y cada turno esperaba en balde el límite de la redacción (750 ms, medido
    2026-10-11); una segunda llamada a la API tras la interpretación tampoco cabe en 1,2 s."""
    if settings.phrasing != "llm" or settings.llm in ("vertex", "workers_ai"):
        return None
    return OllamaModel(
        settings.ollama_url, settings.ollama_model, settings.ollama_timeout_s, num_predict=60
    )


def _default_streaming_factory() -> StreamingFactory | None:
    if not settings.assemblyai_api_key:
        return None
    validate_eu_endpoint(settings.assemblyai_api_base)
    host = settings.assemblyai_streaming_host

    def make(
        sample_rate: int, keyterms: list[str], language: str | list[str] | None = None
    ) -> AssemblyAIStreamingSession:
        return AssemblyAIStreamingSession(
            settings.assemblyai_api_key,
            sample_rate,
            host,
            eot_confidence=settings.assemblyai_eot_confidence,
            eot_silence_ms=settings.assemblyai_eot_silence_ms,
            max_silence_ms=settings.assemblyai_max_silence_ms,
            keyterms=keyterms,
            # One language, or the enabled ones while the call's is still being detected
            language=[i18n.get(c).stt.language_code for c in language]
            if isinstance(language, list)
            else i18n.get(language).stt.language_code
            if language
            else None,
            language_detection=isinstance(language, list),
            speech_model=settings.assemblyai_model,
        )

    return make


AUTO: Any = object()  # centinela: "según settings"


MIN_SECRET_LENGTH = 32
"""`BC_SESSION_SECRET` firma las URLs de audio. 32 caracteres es el mínimo razonable para un
HMAC-SHA256; el README manda generarlo con `secrets.token_urlsafe(48)`."""


def signing_secret() -> str:
    """Secreto con el que se firman las URLs de audio de los expedientes.

    Hasta 2026-08-29 aquí había un `settings.sesion_secreto or ("dev-" + tipo_de_repo)`. Ese
    respaldo anulaba la comprobación de `AudioStore`, que nunca llegaba a ver un secreto vacío:
    sin `BC_SESSION_SECRET` las grabaciones quedaban firmadas con la constante
    `"dev-RepositorioPostgres"`, deducible del código, y cualquiera podía forjar `?exp=&firma=` y
    descargar conversaciones enteras de vecinos.

    Ahora, en el modo real (`BC_REPOSITORY=postgres`) se falla al arrancar: es preferible que la
    API no levante a que sirva audio con una firma que no protege nada. En desarrollo se usa un
    secreto aleatorio por proceso; las URLs ya emitidas dejan de valer al reiniciar, que es
    justo lo que se espera de un secreto efímero.
    """
    if len(settings.session_secret) >= MIN_SECRET_LENGTH:
        return settings.session_secret
    if settings.repository == "postgres":
        raise RuntimeError(
            f"BC_SESSION_SECRET es obligatorio y necesita {MIN_SECRET_LENGTH} caracteres o "
            "más: firma las URLs con las que se descargan las grabaciones de las llamadas. "
            'Genera uno con: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    logger.warning(
        "Sin BC_SESSION_SECRET utilizable: el audio se firma con un secreto aleatorio de este "
        "proceso. Sirve para desarrollo; las URLs firmadas caducan al reiniciar la API."
    )
    return secrets.token_urlsafe(48)


def create_app(
    deps: Dependencies | None = None,
    stt: VoiceRecognizer | None = None,
    streaming_factory: StreamingFactory | None | Any = AUTO,
    oidc: OidcClient | None = None,
) -> FastAPI:
    from building_call.repos.postgres import PostgresRepository

    if deps is None:
        if settings.repository == "postgres":
            repo: Any = PostgresRepository(settings.database_url)
        else:
            repo = MemoryRepository()
        deps = Dependencies(repo=repo, llm=create_llm())
    if stt is None:
        stt = (
            WhisperRecognizer(
                settings.whisper_model, settings.whisper_device, settings.whisper_compute
            )
            if settings.stt == "whisper"
            else UnavailableRecognizer()
        )
    if streaming_factory is AUTO:
        streaming_factory = _default_streaming_factory()
    recognizer: VoiceRecognizer = stt
    manager = CallManager(deps, phraser=create_phraser())

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        # Calienta modelo y STT para que el primer turno real no pague la carga
        if hasattr(deps.llm, "warm_up"):
            await deps.llm.warm_up()
        if hasattr(recognizer, "warm_up"):
            await recognizer.warm_up()
        if not access.providers():
            logger.warning(
                "Sin Google ni Microsoft configurados nadie puede entrar en la consola "
                "(BC_GOOGLE_CLIENT_ID / BC_MICROSOFT_CLIENT_ID en backend/.env)"
            )
        # Las plantillas publicadas se cargan una vez: durante la llamada, `render` solo mira
        # un diccionario en memoria (el presupuesto de latencia no admite una consulta por frase)
        try:
            templates.load(await deps.repo.list_overridden_templates())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - sin sobrescrituras se usa el texto del código
            logger.exception("No se pudieron cargar las plantillas publicadas")
        if hasattr(tts, "warm_up"):
            await tts.warm_up()
        purge_task = asyncio.create_task(periodic_purge())
        yield
        purge_task.cancel()

    app = FastAPI(title="Building Call Assistant", version="0.4.0", lifespan=lifespan)
    app.state.repo = deps.repo
    # Who and in which request: read by the triggers and the access log
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Consola operativa: acceso (usuarios internos + cookie de sesión), APIs protegidas y audio
    secret = signing_secret()
    if isinstance(deps.repo, PostgresRepository):
        access_log = AccessLog(PostgresAccessStore(deps.repo.sessions))
    else:
        # Without PostgreSQL there are no triggers: accesses are kept in memory, changes nowhere
        access_log = AccessLog(MemoryAccessStore())
    access = Access(deps.repo, settings, oidc, access_log)  # type: ignore[arg-type]
    app.state.access_log = access_log
    audio_store = AudioStore(
        settings.audio_dir, secret, settings.audio_retention_days, settings.audio_url_seconds
    )
    app.include_router(access.router())
    app.include_router(access.users_router())

    # Telefonía: puente proveedor-agnóstico + adaptador Twilio. Sin configurar, solo expone /salud
    tts = create_tts(settings)

    def telephony_ready() -> str | None:
        return not_ready_reason(settings, tts.available(), streaming_factory is not None)

    async def bridge_factory(
        channel: TwilioChannel,
        phone: str | None,
        existing_call_id: str | None = None,
        language: str | None = None,
        persona: str | None = None,
    ) -> CallBridge:
        assert streaming_factory is not None
        if existing_call_id:
            # The console opened it, in the language it chose
            language = (await manager.get(existing_call_id)).get("language") or language
        code = i18n.normalize_code(language)
        return CallBridge(
            manager,
            streaming_factory,
            tts,
            channel,
            vocabulary=await stt_vocabulary(deps.repo, code),
            phone=phone,
            repo=deps.repo,
            audio_store=audio_store,
            bargein_words=settings.telephony_bargein_words,
            existing_call_id=existing_call_id,
            language=code,
            persona=persona if persona in personas.personas() else None,
        )

    app.include_router(create_twilio_router(settings, bridge_factory, telephony_ready))
    # Las APIs de la llamada de la consola (iniciar, mensajes, voz, métricas) exigen sesión: la
    # telefonía entra por su propio adaptador firmado, así que ya no hay motivo para dejarlas abiertas
    protected = [Depends(access.requires())]
    # Las rutas de una llamada también las usa el agente de voz de LiveKit, con su secreto
    protected_call = [Depends(requires_user_or_agent(access))]
    app.include_router(create_livekit_router(access))
    app.include_router(create_audit_router(access))

    @app.post("/api/telephony/twilio/browser-token")
    async def twilio_browser_token(user: Any = Depends(access.requires())) -> dict[str, str]:
        """Access Token so the console can call the agent through Twilio from the browser."""
        reason = telephony_ready() or browser_ready_reason(settings)
        if reason is not None:
            raise HTTPException(503, reason)
        identity = console_identity(user.username)
        token = voice_access_token(
            settings.twilio_account_sid,
            settings.twilio_api_key_sid,
            settings.twilio_api_key_secret,
            settings.twilio_twiml_app_sid,
            identity,
        )
        return {"token": token, "identity": identity}

    @app.get("/api/languages")
    async def languages() -> dict[str, Any]:
        """The registry, for the console: what each language is and who speaks it."""
        return {
            "default": i18n.default_code(),
            "languages": [
                {
                    "code": lang.code,
                    "locale": lang.locale,
                    "name": lang.name,
                    "native_name": lang.native_name,
                    "rtl": lang.rtl,
                    "browser_lang": lang.tts.browser_lang,
                    "stt": lang.stt.provider,
                    "server_tts": bool(lang.tts.elevenlabs_language),
                    "enabled": lang.code in i18n.enabled(),
                }
                for lang in i18n.languages().values()
            ],
            "detection": settings.language_detection,
        }

    @app.get("/api/personas")
    async def list_personas() -> list[dict[str, Any]]:
        """Client voice personas, for the console: who answers in each language."""
        return [
            {
                "id": p.id,
                "client": p.client,
                "default": p.id == personas.default_id(),
                "languages": {
                    code: {"agent_name": lang.agent_name, "has_voice": bool(lang.voice)}
                    for code, lang in p.languages.items()
                },
            }
            for p in personas.personas().values()
        ]

    @app.get("/api/telephony/state")
    async def telephony_state(_: Any = Depends(access.requires())) -> dict[str, Any]:  # noqa: B008
        reason = telephony_ready()
        return {
            "provider": settings.telephony,
            "items": reason is None,
            "reason": reason,
            "number": settings.twilio_number or None,
            "public_url": settings.public_url or None,
            "tts": tts.name,
            "tts_available": tts.available(),
            "stt_streaming": streaming_factory is not None,
        }

    app.include_router(
        create_operations_router(deps.repo, access, manager.metrics, audio_store)  # type: ignore[arg-type]
    )
    # Flujo del asistente: grafo, plantillas editables, prompts (solo lectura) y catálogos
    app.include_router(create_flow_router(deps.repo, deps.repo, access))  # type: ignore[arg-type]
    # Bandeja del administrador: incidencias pendientes de su decisión y sus acciones
    app.include_router(create_inbox_router(deps.repo, deps.repo, access))  # type: ignore[arg-type]

    # Catálogo: lo que el asistente consulta en cada llamada, editable por owner y administrador
    app.include_router(
        create_catalog_router(deps.repo, deps.repo, deps.repo, access)  # type: ignore[arg-type]
    )

    async def periodic_purge() -> None:
        while True:
            try:
                with audit_context(AuditContext(actor="system:retention")):
                    await audio_store.purge_expired(deps.repo)  # type: ignore[arg-type]
                    await access_log.close_abandoned()
                    if isinstance(deps.repo, PostgresRepository):
                        await purge_audit(
                            deps.repo.sessions, settings.audit_retention_days, datetime.now(UTC)
                        )
            except Exception:  # noqa: BLE001
                logger.exception("Fallo purgando audio vencido")
            await asyncio.sleep(3600)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        llm_ok = await deps.llm.available() if hasattr(deps.llm, "available") else True
        return {
            "state": "ok",
            "repository": type(deps.repo).__name__,
            "model": deps.llm.name,
            "model_available": llm_ok,
            "stt_server": recognizer.name,
            "stt_available": recognizer.available(),
            "stt_streaming": "assemblyai-eu" if streaming_factory else None,
            "livekit": livekit_configured(),
            "access": access.mode,
            "telephony": settings.telephony if telephony_ready() is None else None,
            "twilio_browser": telephony_ready() is None and browser_ready_reason(settings) is None,
            "server_tts": tts.name if tts.available() else None,
            "graphs": sorted(REGISTRY),
            "thresholds_ms": {name: limit for limit, name in THRESHOLDS_MS},
        }

    @app.get("/api/estates", dependencies=protected)
    async def estates() -> list[dict[str, Any]]:
        output = []
        for f in await deps.repo.list_estates():
            homes = await deps.repo.homes_of(f.id)
            output.append({**f.model_dump(), "homes": [v.label() for v in homes]})
        return output

    def speculate(call_id: str, partial: str) -> None:
        """Lanza la interpretación LLM sobre el transcript parcial, antes del fin de turno.

        Solo una en vuelo: un parcial nuevo cancela la anterior. Si el final coincide con el
        último parcial, `_interpret` reutiliza el resultado (medido: ahorra ~0,5-0,9 s)."""
        if len(partial.split()) < 2:
            return
        key = speculation_key(partial)
        own = deps.speculations.get(call_id, {})
        if key in own:
            return
        try:
            current_state = manager.get_local(call_id)
        except CallNotFound:
            return
        deps.discard_speculations(call_id)
        deps.speculations[call_id] = {}
        deps.speculations[call_id][key] = asyncio.ensure_future(
            interpret(
                deps.llm,
                current_state.get("last_question"),
                partial,
                current_state.get("current_node", ""),
                catalog=catalog_for(current_state),
                heard_text=heard_text_if_cut(current_state.get("last_delivery")),
                equipment_list=equipment_for(current_state),
                known=known_context(current_state),
                language=current_state.get("language"),
            )
        )

    @app.get("/api/voice/vocabulary", dependencies=protected_call)
    async def vocabulary(language: str | None = None) -> list[str]:
        """Términos de las fincas para el STT del agente de voz (calles, portales, puertas)."""
        return await stt_vocabulary(deps.repo, i18n.normalize_code(language))

    @app.post("/api/calls/{call_id}/partial", status_code=202, dependencies=protected_call)
    async def partial(call_id: str, body: Message) -> dict[str, bool]:
        """Transcript parcial del agente de voz: adelanta la interpretación (como el WebSocket)."""
        speculate(call_id, body.text)
        return {"accepted": True}

    @app.post("/api/calls", status_code=201, dependencies=protected_call)
    async def begin(body: NewCall) -> dict[str, Any]:
        try:
            return _view(await manager.begin(body.caller_phone, body.language, body.persona))
        except i18n.UnknownLanguage as e:
            raise HTTPException(422, f"Idioma no soportado: {body.language}") from e
        except personas.UnknownPersona as e:
            raise HTTPException(422, f"Persona desconocida: {body.persona}") from e

    @app.post("/api/calls/{call_id}/messages", dependencies=protected_call)
    async def message(call_id: str, body: Message) -> dict[str, Any]:
        try:
            return _view(await manager.message(call_id, body.text))
        except CallNotFound as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.post("/api/calls/{call_id}/audio", dependencies=protected)
    async def audio(
        call_id: str,
        file: UploadFile = File(...),  # noqa: B008
        mime: str | None = Form(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        """Turno por voz (lotes): el servidor transcribe (faster-whisper) y ejecuta el grafo."""
        if not recognizer.available():
            raise HTTPException(
                503, "STT en servidor no disponible; use el reconocimiento del navegador"
            )
        data = await file.read(MAX_AUDIO_BYTES + 1)
        if not data:
            raise HTTPException(400, "Audio vacío")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio demasiado grande para un turno")
        try:
            transcript = await recognizer.transcribe(
                data, mime or file.content_type or "audio/webm"
            )
        except Exception as e:  # noqa: BLE001
            # El detalle va al log, no al cliente (puede llevar rutas o respuestas del proveedor)
            logger.exception("Error de transcripción en %s", call_id)
            raise HTTPException(502, "Error de transcripción") from e
        if not transcript.text:
            try:
                state = await manager.get(call_id)
            except CallNotFound as e:
                raise HTTPException(404, "Llamada no encontrada") from e
            return {
                **_view(state),
                "transcript": "",
                "bot_messages": [],
                "no_voice": True,
                "timings": {"stt_ms": transcript.ms},
            }
        try:
            state = await manager.message(call_id, transcript.text, stt_ms=transcript.ms)
        except CallNotFound as e:
            raise HTTPException(404, "Llamada no encontrada") from e
        return {
            **_view(state),
            "transcript": transcript.text,
            "stt": transcript.detail,
        }

    @app.websocket("/api/calls/{call_id}/voice")
    async def voice(ws: WebSocket, call_id: str) -> None:
        """Turnos por voz en streaming.

        Cliente -> servidor: binario = PCM16 mono (sample_rate en query, 16000 por defecto);
        texto JSON: {"type":"fin_turno"} fuerza el cierre del turno, {"type":"pausa"|"reanudar"}
        silencia la entrada mientras habla el asistente (se sigue enviando silencio al proveedor).
        Servidor -> cliente: {"type":"listo"}, {"type":"parcial","texto"}, {"type":"final","texto",
        "eou_at"}, {"type":"respuesta", ...vista, "tiempos"}, {"type":"error","mensaje"}.
        """
        await ws.accept()
        # Consola o, como en el resto de rutas de llamada, el secreto del agente (banco de voz)
        if is_agent(ws):
            set_actor("agent:livekit", "agent")
        elif await access.user_from_request(ws) is None:  # type: ignore[arg-type]
            await ws.send_json({"type": "error", "message": "Sesión no válida o expirada"})
            await ws.close(code=1008)
            return
        if streaming_factory is None:
            await ws.send_json({"type": "error", "message": "STT en streaming no configurado"})
            await ws.close(code=1011)
            return
        try:
            initial_state = await manager.get(call_id)
        except CallNotFound:
            await ws.send_json({"type": "error", "message": "Llamada no encontrada"})
            await ws.close(code=1008)
            return
        try:
            sample_rate = int(ws.query_params.get("sample_rate", "16000"))
        except ValueError:
            sample_rate = 0
        # El navegador pide 16 kHz, pero AudioContext puede caer al ritmo del equipo (44,1/48/96).
        # El tope solo evita que un cliente reserve memoria arbitraria con el búfer de silencio.
        if not 8000 <= sample_rate <= 192000:
            await ws.send_json({"type": "error", "message": "sample_rate fuera de rango"})
            await ws.close(code=1008)
            return
        language = str(initial_state.get("language") or i18n.default_code())
        # Until the caller's first words decide it, listen to every enabled language
        listen: str | list[str] = (
            language if initial_state.get("language_locked") else list(i18n.enabled())
        )
        session = streaming_factory(
            sample_rate, await stt_vocabulary(deps.repo, language), listen
        )
        try:
            await session.open()
        except Exception as e:  # noqa: BLE001
            logger.warning("No se pudo abrir el STT: %s", e)
            await ws.send_json(
                {"type": "error", "message": "No se pudo abrir el reconocimiento de voz"}
            )
            await ws.close(code=1011)
            return
        await ws.send_json({"type": "ready", "connection_ms": getattr(session, "connection_ms", None)})
        paused = False
        silence = b"\x00" * (int(sample_rate * 0.1) * 2)
        last_voice_at: float | None = None  # último audio con energía recibido del navegador
        # Grabación alineada de las dos voces (retención 14 días). Aquí el bot habla por el
        # navegador y el servidor nunca ve ese audio: su pista se reconstruye al cerrar
        # sintetizando lo que el navegador informa como entregado, anclado a la marca de tiempo
        # en que empezó a sonar. Ver `building_call.recording`.
        recorder = ConversationRecorder()
        bot_turns: list[tuple[int, str]] = []  # (marca en bytes, texto que sonó)
        bot_mark: int | None = None  # puesta en `pause`, consumida en `delivery`
        bot_text = ""  # último mensaje enviado al navegador, por si no llega `delivery`
        if not int(initial_state.get("turn", 0)):
            # El saludo suena mientras se abre el micrófono: se ancla al inicio de la grabación.
            greeting = " ".join(initial_state.get("bot_messages") or []).strip()
            if greeting:
                bot_turns.append((0, greeting))

        async def rebuild_bot_voice() -> None:
            """Sintetiza los turnos del bot y los coloca donde sonaron. Secuencial y en orden a
            propósito: cada turno se ancla a su marca y nunca pisa al anterior, así que escribirlos
            desordenados los desplazaría. Va al cerrar, fuera del camino crítico de la llamada."""
            if not bot_turns or not tts.available():
                return
            for brand, text in sorted(bot_turns):
                pcm = await tts.synthesize(text, language)
                recorder.write_assistant(pcm, int(tts.rate), since=brand)

        async def pump_events() -> None:
            nonlocal bot_text, language
            async for ev in session.events():
                if ev.kind == "partial":
                    await ws.send_json({"type": "partial", "text": ev.text})
                    speculate(call_id, ev.text)
                elif ev.kind == "error":
                    await ws.send_json({"type": "error", "message": ev.text})
                elif ev.kind == "final":
                    if not ev.text:
                        continue
                    t_final = perf_counter()
                    eou_ms = int((t_final - last_voice_at) * 1000) if last_voice_at else None
                    await ws.send_json({"type": "final", "text": ev.text, "eou_ms": eou_ms})
                    try:
                        state = await manager.message(
                            call_id,
                            ev.text,
                            stt_language=ev.detail.get("language_code"),
                            stt_confidence=ev.detail.get("language_confidence"),
                        )
                    except CallNotFound:
                        await ws.send_json({"type": "error", "message": "Llamada no encontrada"})
                        return
                    if state.get("language") != language:
                        # The call switched language: the STT and the bot's voice follow
                        language = str(state.get("language"))
                        if hasattr(session, "set_language"):
                            await session.set_language(language)
                    view = _view(state)
                    view["timings"] = {
                        **view["timings"],
                        "stt_eou_ms": eou_ms,
                        "stt_eou_to_response_ms": int((perf_counter() - t_final) * 1000),
                    }
                    bot_text = " ".join(view.get("bot_messages") or []).strip()
                    await ws.send_json({"type": "reply", **view})
                    if state.get("finished"):
                        return

        async def receive_audio() -> None:
            nonlocal paused, last_voice_at, bot_mark
            while True:
                packet = await ws.receive()
                if packet.get("type") == "websocket.disconnect":
                    return
                if packet.get("bytes") is not None:
                    if not paused and _has_voice(packet["bytes"]):
                        last_voice_at = perf_counter()
                    recorder.write_client(packet["bytes"], sample_rate)
                    await session.send_audio(silence if paused else packet["bytes"])
                elif packet.get("text"):
                    order = json.loads(packet["text"])
                    if order.get("type") == "end_turn":
                        await session.force_end_of_turn()
                    elif order.get("type") == "delivery":
                        if bot_mark is not None:
                            # `delivered_text` ya descuenta lo que cortó un barge-in
                            bot_turns.append(
                                (bot_mark, str(order.get("delivered_text") or bot_text))
                            )
                            bot_mark = None
                        await manager.record_delivery(
                            call_id,
                            int(order.get("turn", 0)),
                            str(order.get("state", "completed")),
                            float(order.get("ratio", 1.0)),
                            order.get("delivered_text"),
                        )
                    elif order.get("type") == "pause":
                        if bot_mark is not None and bot_text:
                            bot_turns.append((bot_mark, bot_text))  # sin `delivery` previa
                        bot_mark = recorder.mark()
                        paused = True
                    elif order.get("type") == "resume":
                        paused = False

        tasks = {
            asyncio.create_task(pump_events(), name="events"),
            asyncio.create_task(receive_audio(), name="audio"),
        }
        try:
            done_tasks, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done_tasks:
                logger.warning(
                    "WebSocket de voz: tarea %s terminó (cierre STT=%s)",
                    t.get_name(),
                    getattr(session, "closing", None),
                )
                exc = t.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    logger.exception(
                        "Fallo en la tarea %s del WebSocket de voz", t.get_name(), exc_info=exc
                    )
                    try:
                        await ws.send_json({"type": "error", "message": f"{t.get_name()}: {exc}"})
                    except Exception:  # noqa: BLE001
                        pass
            for t in pending:
                t.cancel()
        finally:
            deps.discard_speculations(call_id)
            await session.close()
            if bot_mark is not None and bot_text:
                bot_turns.append((bot_mark, bot_text))
            try:
                await rebuild_bot_voice()
            except Exception:  # noqa: BLE001
                # Sin voz del bot la llamada se guarda igual: mejor una pista que ninguna
                logger.exception("No se pudo reconstruir la voz del bot en %s", call_id)
            try:
                await audio_store.save_conversation(deps.repo, call_id, recorder)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo guardar el audio de la llamada %s", call_id)
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    @app.post("/api/calls/{call_id}/delivery", dependencies=protected_call)
    async def delivery(call_id: str, body: Delivery) -> dict[str, Any]:
        try:
            state = await manager.record_delivery(
                call_id, body.turn, body.state, body.ratio, body.delivered_text
            )
        except CallNotFound as e:
            raise HTTPException(404, "Llamada no encontrada") from e
        return {"last_delivery": state.get("last_delivery")}

    @app.post("/api/voz-bot", dependencies=protected)
    async def bot_voice(body: BotVoice) -> StreamingResponse:
        """La consola habla con la misma voz que el teléfono: PCM16 mono por trozos (`X-Tasa`)."""
        if not tts.available():
            raise HTTPException(503, "No hay voz de servidor configurada")
        chunks = fragments(tts, body.text, i18n.normalize_code(body.language))
        # El primer trozo se pide antes de responder: un fallo del proveedor es un 502 limpio
        # (la consola vuelve a la voz del navegador) y no un audio cortado a medias
        try:
            primero = await anext(chunks, b"")
        except Exception as e:  # noqa: BLE001
            logger.warning("Voz del bot no disponible: %s", e)
            raise HTTPException(502, "La voz del servidor no responde") from e

        async def audio() -> Any:
            yield primero
            async for chunk in chunks:
                yield chunk

        return StreamingResponse(
            audio(),
            media_type="application/octet-stream",
            headers={"X-Tasa": str(tts.rate), "Cache-Control": "no-store"},
        )

    @app.post("/api/calls/{call_id}/metrics", dependencies=protected_call)
    async def client_metrics(call_id: str, body: ClientMetrics) -> dict[str, Any]:
        t = manager.metrics.complete_client(call_id, body.turn, body.model_dump())
        if t is None:
            raise HTTPException(404, "Turno no encontrado")
        return t.as_dict()

    @app.get("/api/calls/{call_id}", dependencies=protected_call)
    async def get(call_id: str) -> dict[str, Any]:
        try:
            return _view(await manager.get(call_id))
        except CallNotFound as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.get("/api/calls/{call_id}/metrics", dependencies=protected)
    async def call_metrics(call_id: str) -> dict[str, Any]:
        return {
            "aggregates": manager.metrics.aggregates(call_id),
            "turns": [t.as_dict() for t in manager.metrics.turns(call_id)],
        }

    @app.get("/api/metrics", dependencies=protected)
    async def metrics() -> dict[str, Any]:
        output: dict[str, Any] = {
            "aggregates": manager.metrics.aggregates(),
            "last_turns": [t.as_dict() for t in manager.metrics.turns()[-50:]],
        }
        if isinstance(deps.llm, RaceModel):
            output["race_winners"] = deps.llm.winners
        return output

    @app.get("/api/incidents", dependencies=protected)
    async def incidents() -> list[dict[str, Any]]:
        rows = await deps.repo.list_incidents()
        await access_log.list(IncidentSQL, {}, rows=len(rows))
        return [i.model_dump(mode="json") for i in rows]

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("building_call.api.main:app", host="127.0.0.1", port=8000, reload=True)
