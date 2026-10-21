"""API HTTP/WebSocket consumida por la consola de validación (y, más adelante, por la telefonía)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
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

from building_call.acceso import Acceso
from building_call.audio_store import AlmacenAudio
from building_call.flujo_api import crear_router_flujo
from building_call.grabacion import GrabadorConversacion
from building_call.graph import plantillas
from building_call.graph.estado import EstadoLlamada
from building_call.graph.ledger import texto_oido_si_cortado
from building_call.graph.nodos import (
    Dependencias,
    catalogo_para,
    clave_especulacion,
    equipos_para,
)
from building_call.graph.registro import REGISTRO
from building_call.livekit_api import (
    crear_router_livekit,
    livekit_configurado,
    requiere_usuario_o_agente,
)
from building_call.llm.carrera import ModeloCarrera
from building_call.llm.interpretacion import interpretar
from building_call.llm.ollama import ModeloOllama
from building_call.llm.vertex import ModeloVertex
from building_call.llm.workers_ai import ModeloWorkersAI
from building_call.metricas import UMBRALES_MS
from building_call.oidc import ClienteOidc
from building_call.operaciones_api import crear_router_operaciones
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas, LlamadaNoEncontrada
from building_call.settings import settings
from building_call.stt import ReconocedorNoDisponible, ReconocedorVoz, ReconocedorWhisper
from building_call.stt_streaming import SesionStreamingAssemblyAI, validar_endpoint_eu
from building_call.telefonia.configurar import motivo_no_listo
from building_call.telefonia.puente import PuenteLlamada
from building_call.telefonia.tts import crear_tts, fragmentos
from building_call.telefonia.twilio import CanalTwilio, crear_router_twilio

logger = logging.getLogger(__name__)


def _hay_voz(pcm: bytes, umbral: int = 300) -> bool:
    """RMS de PCM16 por encima del umbral: sirve para medir el fin de voz en el servidor."""
    import audioop

    try:
        return audioop.rms(pcm, 2) > umbral
    except audioop.error:
        return False


# Fábrica de sesiones de streaming: (sample_rate, keyterms) -> sesión con
# abrir/enviar_audio/eventos/cerrar
FabricaStreaming = Callable[[int, list[str]], Any]

_ORDINALES_VOZ = {
    "0": "bajo", "1": "primero", "2": "segundo", "3": "tercero", "4": "cuarto", "5": "quinto",
    "6": "sexto", "7": "séptimo", "8": "octavo", "9": "noveno", "10": "décimo",
}  # fmt: skip


async def vocabulario_stt(repo: Any) -> list[str]:
    """Términos que el STT debe reconocer bien: direcciones, portales, pisos y puertas reales."""
    terminos: list[str] = ["portal", "piso", "puerta", "ático", "bajo", "sí", "no"]
    fincas = await repo.listar_fincas()
    puertas: set[str] = set()
    pisos: set[str] = set()
    portales: set[str] = set()
    for f in fincas:
        terminos.append(f.direccion)
        for v in await repo.viviendas_de(f.id):
            puertas.add(v.puerta)
            pisos.add(v.piso)
            if v.portal:
                portales.add(v.portal)
    terminos += [f"portal {p}" for p in sorted(portales)]
    terminos += [f"puerta {p}" for p in sorted(puertas)]
    for piso in sorted(pisos, key=lambda x: int(x) if x.isdigit() else 99):
        ordinal = _ORDINALES_VOZ.get(piso, piso)
        terminos += [f"{ordinal} {p}" for p in sorted(puertas)]
    return list(dict.fromkeys(terminos))[:100]


class NuevaLlamada(BaseModel):
    telefono_llamante: str | None = None


class Mensaje(BaseModel):
    texto: str


class VozBot(BaseModel):
    texto: str = Field(min_length=1, max_length=800)  # cada carácter se paga en una voz por API


class Entrega(BaseModel):
    """Ledger de salida del navegador: qué parte del mensaje del turno llegó a sonar."""

    turno: int
    estado: str  # completada | interrumpida | no_reproducida
    ratio: float = 1.0
    texto_entregado: str | None = None


class MetricasCliente(BaseModel):
    turno: int
    vad_silencio_ms: int | None = None
    red_ms: int | None = None
    tts_inicio_ms: int | None = None
    respuesta_ms: int | None = None
    stt_cliente: str | None = None
    extra: dict[str, Any] = {}


def _vista(estado: EstadoLlamada) -> dict[str, Any]:
    campos = [
        "finca_id", "finca_direccion", "vivienda_id", "vivienda_etiqueta", "nombre_declarado",
        "solicitud", "categoria", "urgencia", "urgencia_declarada", "resumen", "confirmado",
        "especialista_id", "especialista_nombre", "canal_aviso", "destino_aviso", "incidencia_id",
        "resultado", "motivo_revision", "configuracion_id", "grafo", "intentos_finca", "intentos_vivienda",
        "actuacion", "equipo_id", "sugerencia", "objecion",
    ]  # fmt: skip
    return {
        "llamada_id": estado["llamada_id"],
        "turno": estado.get("turno", 0),
        "nodo_actual": estado.get("nodo_actual"),
        "esperando_usuario": estado.get("esperando_usuario", False),
        "finalizada": estado.get("finalizada", False),
        "mensajes_bot": estado.get("mensajes_bot", []),
        "historial": estado.get("historial", []),
        "traza": estado.get("traza", []),
        "validaciones": estado.get("validaciones", []),
        "senales_confianza": estado.get("senales_confianza"),
        "interpretacion": estado.get("interpretacion"),
        "pendientes": estado.get("pendientes", {}),
        "entrega_ultima": estado.get("entrega_ultima"),
        "piezas": estado.get("piezas_bot", []),
        "tiempos": estado.get("tiempos_turno", {}),
        "configuracion": estado.get("configuracion"),
        "datos": {c: estado.get(c) for c in campos},
    }


def crear_llm() -> Any:
    """Selecciona el proveedor LLM según BC_LLM."""
    local = ModeloOllama(
        settings.ollama_url,
        settings.ollama_modelo,
        settings.ollama_timeout_s,
        num_predict=settings.ollama_num_predict,
        num_ctx=settings.ollama_num_ctx,
    )
    modo = settings.llm
    hay_cloudflare = bool(settings.cloudflare_account_id and settings.cloudflare_api_token)
    if modo == "auto":
        modo = "carrera" if hay_cloudflare else "ollama"
    if modo == "ollama":
        return local
    if modo == "vertex":
        return ModeloVertex(
            settings.vertex_proyecto,
            settings.vertex_modelo,
            ubicacion=settings.vertex_ubicacion,
            ruta_credenciales=settings.vertex_credenciales,
            timeout_s=settings.vertex_timeout_s,
        )
    nube = ModeloWorkersAI(
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_modelo,
        timeout_s=settings.cloudflare_timeout_s,
    )
    if modo == "workers_ai":
        return nube
    if modo == "carrera":
        return ModeloCarrera(
            nube, local, preferido=0, espera_extra_ms=settings.carrera_espera_extra_ms
        )
    raise ValueError(f"BC_LLM desconocido: {settings.llm}")


def crear_redactor() -> ModeloOllama | None:
    """Redacción con el modelo local y salida corta (la carrera con el 27B tardaba 1,5 s).

    Solo cuando Ollama ya forma parte del despliegue: con un proveedor por API (Vertex, Workers
    AI) no hay Ollama y cada turno esperaba en balde el límite de la redacción (750 ms, medido
    2026-10-11); una segunda llamada a la API tras la interpretación tampoco cabe en 1,2 s."""
    if settings.redaccion != "llm" or settings.llm in ("vertex", "workers_ai"):
        return None
    return ModeloOllama(
        settings.ollama_url, settings.ollama_modelo, settings.ollama_timeout_s, num_predict=60
    )


def _fabrica_streaming_por_defecto() -> FabricaStreaming | None:
    if not settings.assemblyai_api_key:
        return None
    validar_endpoint_eu(settings.assemblyai_api_base)
    host = settings.assemblyai_streaming_host

    def crear(sample_rate: int, keyterms: list[str]) -> SesionStreamingAssemblyAI:
        return SesionStreamingAssemblyAI(
            settings.assemblyai_api_key,
            sample_rate,
            host,
            eot_confianza=settings.assemblyai_eot_confianza,
            eot_silencio_ms=settings.assemblyai_eot_silencio_ms,
            max_silencio_ms=settings.assemblyai_max_silencio_ms,
            keyterms=keyterms,
        )

    return crear


AUTO: Any = object()  # centinela: "según settings"


LARGO_MINIMO_SECRETO = 32
"""`BC_SESION_SECRETO` firma las URLs de audio. 32 caracteres es el mínimo razonable para un
HMAC-SHA256; el README manda generarlo con `secrets.token_urlsafe(48)`."""


def secreto_de_firma() -> str:
    """Secreto con el que se firman las URLs de audio de los expedientes.

    Hasta 2026-08-29 aquí había un `settings.sesion_secreto or ("dev-" + tipo_de_repo)`. Ese
    respaldo anulaba la comprobación de `AlmacenAudio`, que nunca llegaba a ver un secreto vacío:
    sin `BC_SESION_SECRETO` las grabaciones quedaban firmadas con la constante
    `"dev-RepositorioPostgres"`, deducible del código, y cualquiera podía forjar `?exp=&firma=` y
    descargar conversaciones enteras de vecinos.

    Ahora, en el modo real (`BC_REPOSITORIO=postgres`) se falla al arrancar: es preferible que la
    API no levante a que sirva audio con una firma que no protege nada. En desarrollo se usa un
    secreto aleatorio por proceso; las URLs ya emitidas dejan de valer al reiniciar, que es
    justo lo que se espera de un secreto efímero.
    """
    if len(settings.sesion_secreto) >= LARGO_MINIMO_SECRETO:
        return settings.sesion_secreto
    if settings.repositorio == "postgres":
        raise RuntimeError(
            f"BC_SESION_SECRETO es obligatorio y necesita {LARGO_MINIMO_SECRETO} caracteres o "
            "más: firma las URLs con las que se descargan las grabaciones de las llamadas. "
            'Genera uno con: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    logger.warning(
        "Sin BC_SESION_SECRETO utilizable: el audio se firma con un secreto aleatorio de este "
        "proceso. Sirve para desarrollo; las URLs firmadas caducan al reiniciar la API."
    )
    return secrets.token_urlsafe(48)


def crear_app(
    deps: Dependencias | None = None,
    stt: ReconocedorVoz | None = None,
    fabrica_streaming: FabricaStreaming | None | Any = AUTO,
    oidc: ClienteOidc | None = None,
) -> FastAPI:
    if deps is None:
        if settings.repositorio == "postgres":
            from building_call.repos.postgres import RepositorioPostgres

            repo: Any = RepositorioPostgres(settings.database_url)
        else:
            repo = RepositorioMemoria()
        deps = Dependencias(repo=repo, llm=crear_llm())
    if stt is None:
        stt = (
            ReconocedorWhisper(
                settings.whisper_modelo, settings.whisper_dispositivo, settings.whisper_computo
            )
            if settings.stt == "whisper"
            else ReconocedorNoDisponible()
        )
    if fabrica_streaming is AUTO:
        fabrica_streaming = _fabrica_streaming_por_defecto()
    reconocedor: ReconocedorVoz = stt
    gestor = GestorLlamadas(deps, redactor=crear_redactor())

    @asynccontextmanager
    async def ciclo_vida(_: FastAPI):  # type: ignore[no-untyped-def]
        # Calienta modelo y STT para que el primer turno real no pague la carga
        if hasattr(deps.llm, "calentar"):
            await deps.llm.calentar()
        if hasattr(reconocedor, "calentar"):
            await reconocedor.calentar()
        if not acceso.proveedores():
            logger.warning(
                "Sin Google ni Microsoft configurados nadie puede entrar en la consola "
                "(BC_GOOGLE_CLIENT_ID / BC_MICROSOFT_CLIENT_ID en backend/.env)"
            )
        # Las plantillas publicadas se cargan una vez: durante la llamada, `render` solo mira
        # un diccionario en memoria (el presupuesto de latencia no admite una consulta por frase)
        try:
            plantillas.cargar(await deps.repo.listar_plantillas_sobrescritas())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - sin sobrescrituras se usa el texto del código
            logger.exception("No se pudieron cargar las plantillas publicadas")
        if hasattr(tts, "precalentar"):
            await tts.precalentar()
        tarea_purga = asyncio.create_task(purga_periodica())
        yield
        tarea_purga.cancel()

    app = FastAPI(title="Building Call Assistant", version="0.4.0", lifespan=ciclo_vida)
    app.state.repo = deps.repo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Consola operativa: acceso (usuarios internos + cookie de sesión), APIs protegidas y audio
    secreto = secreto_de_firma()
    acceso = Acceso(deps.repo, settings, oidc)  # type: ignore[arg-type]
    almacen_audio = AlmacenAudio(
        settings.audio_dir, secreto, settings.audio_retencion_dias, settings.audio_url_segundos
    )
    app.include_router(acceso.router())
    app.include_router(acceso.router_usuarios())

    # Telefonía: puente proveedor-agnóstico + adaptador Twilio. Sin configurar, solo expone /salud
    tts = crear_tts(settings)

    def telefonia_lista() -> str | None:
        return motivo_no_listo(settings, tts.disponible(), fabrica_streaming is not None)

    async def fabrica_puente(canal: CanalTwilio, telefono: str | None) -> PuenteLlamada:
        assert fabrica_streaming is not None
        return PuenteLlamada(
            gestor,
            fabrica_streaming,
            tts,
            canal,
            vocabulario=await vocabulario_stt(deps.repo),
            telefono=telefono,
            repo=deps.repo,
            almacen_audio=almacen_audio,
            palabras_bargein=settings.telefonia_palabras_bargein,
        )

    app.include_router(crear_router_twilio(settings, fabrica_puente, telefonia_lista))
    # Las APIs de la llamada de la consola (iniciar, mensajes, voz, métricas) exigen sesión: la
    # telefonía entra por su propio adaptador firmado, así que ya no hay motivo para dejarlas abiertas
    protegido = [Depends(acceso.requiere())]
    # Las rutas de una llamada también las usa el agente de voz de LiveKit, con su secreto
    llamada_protegida = [Depends(requiere_usuario_o_agente(acceso))]
    app.include_router(crear_router_livekit(acceso))

    @app.get("/api/telefonia/estado")
    async def telefonia_estado(_: Any = Depends(acceso.requiere())) -> dict[str, Any]:  # noqa: B008
        motivo = telefonia_lista()
        return {
            "proveedor": settings.telefonia,
            "lista": motivo is None,
            "motivo": motivo,
            "numero": settings.twilio_numero or None,
            "url_publica": settings.url_publica or None,
            "tts": tts.nombre,
            "tts_disponible": tts.disponible(),
            "stt_streaming": fabrica_streaming is not None,
        }

    app.include_router(
        crear_router_operaciones(deps.repo, acceso, gestor.metricas, almacen_audio)  # type: ignore[arg-type]
    )
    # Flujo del asistente: grafo, plantillas editables, prompts (solo lectura) y catálogos
    app.include_router(crear_router_flujo(deps.repo, deps.repo, acceso))  # type: ignore[arg-type]

    async def purga_periodica() -> None:
        while True:
            try:
                await almacen_audio.purgar_vencidas(deps.repo)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                logger.exception("Fallo purgando audio vencido")
            await asyncio.sleep(3600)

    @app.get("/api/salud")
    async def salud() -> dict[str, Any]:
        llm_ok = await deps.llm.disponible() if hasattr(deps.llm, "disponible") else True
        return {
            "estado": "ok",
            "repositorio": type(deps.repo).__name__,
            "modelo": deps.llm.nombre,
            "modelo_disponible": llm_ok,
            "stt_servidor": reconocedor.nombre,
            "stt_disponible": reconocedor.disponible(),
            "stt_streaming": "assemblyai-eu" if fabrica_streaming else None,
            "livekit": livekit_configurado(),
            "acceso": acceso.modo,
            "telefonia": settings.telefonia if telefonia_lista() is None else None,
            "tts_servidor": tts.nombre if tts.disponible() else None,
            "grafos": sorted(REGISTRO),
            "umbrales_ms": {nombre: limite for limite, nombre in UMBRALES_MS},
        }

    @app.get("/api/fincas", dependencies=protegido)
    async def fincas() -> list[dict[str, Any]]:
        salida = []
        for f in await deps.repo.listar_fincas():
            viviendas = await deps.repo.viviendas_de(f.id)
            salida.append({**f.model_dump(), "viviendas": [v.etiqueta() for v in viviendas]})
        return salida

    def especular(llamada_id: str, parcial: str) -> None:
        """Lanza la interpretación LLM sobre el transcript parcial, antes del fin de turno.

        Solo una en vuelo: un parcial nuevo cancela la anterior. Si el final coincide con el
        último parcial, `_interpretar` reutiliza el resultado (medido: ahorra ~0,5-0,9 s)."""
        if len(parcial.split()) < 2:
            return
        clave = clave_especulacion(parcial)
        propias = deps.especulaciones.get(llamada_id, {})
        if clave in propias:
            return
        try:
            estado_actual = gestor.obtener(llamada_id)
        except LlamadaNoEncontrada:
            return
        deps.descartar_especulaciones(llamada_id)
        deps.especulaciones[llamada_id] = {}
        deps.especulaciones[llamada_id][clave] = asyncio.ensure_future(
            interpretar(
                deps.llm,
                estado_actual.get("ultima_pregunta"),
                parcial,
                estado_actual.get("nodo_actual", ""),
                catalogo=catalogo_para(estado_actual),
                oido=texto_oido_si_cortado(estado_actual.get("entrega_ultima")),
                equipos=equipos_para(estado_actual),
            )
        )

    @app.get("/api/voz/vocabulario", dependencies=llamada_protegida)
    async def vocabulario() -> list[str]:
        """Términos de las fincas para el STT del agente de voz (calles, portales, puertas)."""
        return await vocabulario_stt(deps.repo)

    @app.post("/api/llamadas/{llamada_id}/parcial", status_code=202, dependencies=llamada_protegida)
    async def parcial(llamada_id: str, cuerpo: Mensaje) -> dict[str, bool]:
        """Transcript parcial del agente de voz: adelanta la interpretación (como el WebSocket)."""
        especular(llamada_id, cuerpo.texto)
        return {"aceptado": True}

    @app.post("/api/llamadas", status_code=201, dependencies=llamada_protegida)
    async def iniciar(cuerpo: NuevaLlamada) -> dict[str, Any]:
        return _vista(await gestor.iniciar(cuerpo.telefono_llamante))

    @app.post("/api/llamadas/{llamada_id}/mensajes", dependencies=llamada_protegida)
    async def mensaje(llamada_id: str, cuerpo: Mensaje) -> dict[str, Any]:
        try:
            return _vista(await gestor.mensaje(llamada_id, cuerpo.texto))
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.post("/api/llamadas/{llamada_id}/audio", dependencies=protegido)
    async def audio(
        llamada_id: str,
        fichero: UploadFile = File(...),  # noqa: B008
        mime: str | None = Form(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        """Turno por voz (lotes): el servidor transcribe (faster-whisper) y ejecuta el grafo."""
        if not reconocedor.disponible():
            raise HTTPException(
                503, "STT en servidor no disponible; use el reconocimiento del navegador"
            )
        datos = await fichero.read()
        if not datos:
            raise HTTPException(400, "Audio vacío")
        try:
            transcripcion = await reconocedor.transcribir(
                datos, mime or fichero.content_type or "audio/webm"
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Error de transcripción: {e}") from e
        if not transcripcion.texto:
            try:
                estado = gestor.obtener(llamada_id)
            except LlamadaNoEncontrada as e:
                raise HTTPException(404, "Llamada no encontrada") from e
            return {
                **_vista(estado),
                "transcripcion": "",
                "mensajes_bot": [],
                "sin_voz": True,
                "tiempos": {"stt_ms": transcripcion.ms},
            }
        try:
            estado = await gestor.mensaje(llamada_id, transcripcion.texto, stt_ms=transcripcion.ms)
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e
        return {
            **_vista(estado),
            "transcripcion": transcripcion.texto,
            "stt": transcripcion.detalle,
        }

    @app.websocket("/api/llamadas/{llamada_id}/voz")
    async def voz(ws: WebSocket, llamada_id: str) -> None:
        """Turnos por voz en streaming.

        Cliente -> servidor: binario = PCM16 mono (sample_rate en query, 16000 por defecto);
        texto JSON: {"type":"fin_turno"} fuerza el cierre del turno, {"type":"pausa"|"reanudar"}
        silencia la entrada mientras habla el asistente (se sigue enviando silencio al proveedor).
        Servidor -> cliente: {"type":"listo"}, {"type":"parcial","texto"}, {"type":"final","texto",
        "eou_at"}, {"type":"respuesta", ...vista, "tiempos"}, {"type":"error","mensaje"}.
        """
        await ws.accept()
        if await acceso.usuario_de_peticion(ws) is None:  # type: ignore[arg-type]
            await ws.send_json({"type": "error", "mensaje": "Sesión no válida o expirada"})
            await ws.close(code=1008)
            return
        if fabrica_streaming is None:
            await ws.send_json({"type": "error", "mensaje": "STT en streaming no configurado"})
            await ws.close(code=1011)
            return
        try:
            estado_inicial = gestor.obtener(llamada_id)
        except LlamadaNoEncontrada:
            await ws.send_json({"type": "error", "mensaje": "Llamada no encontrada"})
            await ws.close(code=1008)
            return
        try:
            sample_rate = int(ws.query_params.get("sample_rate", "16000"))
        except ValueError:
            sample_rate = 0
        # El navegador pide 16 kHz, pero AudioContext puede caer al ritmo del equipo (44,1/48/96).
        # El tope solo evita que un cliente reserve memoria arbitraria con el búfer de silencio.
        if not 8000 <= sample_rate <= 192000:
            await ws.send_json({"type": "error", "mensaje": "sample_rate fuera de rango"})
            await ws.close(code=1008)
            return
        sesion = fabrica_streaming(sample_rate, await vocabulario_stt(deps.repo))
        try:
            await sesion.abrir()
        except Exception as e:  # noqa: BLE001
            await ws.send_json({"type": "error", "mensaje": f"No se pudo abrir el STT: {e}"})
            await ws.close(code=1011)
            return
        await ws.send_json({"type": "listo", "conexion_ms": getattr(sesion, "conexion_ms", None)})
        pausado = False
        silencio = b"\x00" * (int(sample_rate * 0.1) * 2)
        ultima_voz_at: float | None = None  # último audio con energía recibido del navegador
        # Grabación alineada de las dos voces (retención 14 días). Aquí el bot habla por el
        # navegador y el servidor nunca ve ese audio: su pista se reconstruye al cerrar
        # sintetizando lo que el navegador informa como entregado, anclado a la marca de tiempo
        # en que empezó a sonar. Ver `building_call.grabacion`.
        grabador = GrabadorConversacion()
        turnos_bot: list[tuple[int, str]] = []  # (marca en bytes, texto que sonó)
        marca_bot: int | None = None  # puesta en `pausa`, consumida en `entrega`
        texto_bot = ""  # último mensaje enviado al navegador, por si no llega `entrega`
        if not int(estado_inicial.get("turno", 0)):
            # El saludo suena mientras se abre el micrófono: se ancla al inicio de la grabación.
            saludo = " ".join(estado_inicial.get("mensajes_bot") or []).strip()
            if saludo:
                turnos_bot.append((0, saludo))

        async def reconstruir_voz_bot() -> None:
            """Sintetiza los turnos del bot y los coloca donde sonaron. Secuencial y en orden a
            propósito: cada turno se ancla a su marca y nunca pisa al anterior, así que escribirlos
            desordenados los desplazaría. Va al cerrar, fuera del camino crítico de la llamada."""
            if not turnos_bot or not tts.disponible():
                return
            for marca, texto in sorted(turnos_bot):
                pcm = await tts.sintetizar(texto)
                grabador.escribir_asistente(pcm, int(tts.tasa), desde=marca)

        async def bombear_eventos() -> None:
            nonlocal texto_bot
            async for ev in sesion.eventos():
                if ev.tipo == "parcial":
                    await ws.send_json({"type": "parcial", "texto": ev.texto})
                    especular(llamada_id, ev.texto)
                elif ev.tipo == "error":
                    await ws.send_json({"type": "error", "mensaje": ev.texto})
                elif ev.tipo == "final":
                    if not ev.texto:
                        continue
                    t_final = perf_counter()
                    eou_ms = int((t_final - ultima_voz_at) * 1000) if ultima_voz_at else None
                    await ws.send_json({"type": "final", "texto": ev.texto, "eou_ms": eou_ms})
                    try:
                        estado = await gestor.mensaje(llamada_id, ev.texto)
                    except LlamadaNoEncontrada:
                        await ws.send_json({"type": "error", "mensaje": "Llamada no encontrada"})
                        return
                    vista = _vista(estado)
                    vista["tiempos"] = {
                        **vista["tiempos"],
                        "stt_eou_ms": eou_ms,
                        "stt_eou_a_respuesta_ms": int((perf_counter() - t_final) * 1000),
                    }
                    texto_bot = " ".join(vista.get("mensajes_bot") or []).strip()
                    await ws.send_json({"type": "respuesta", **vista})
                    if estado.get("finalizada"):
                        return

        async def recibir_audio() -> None:
            nonlocal pausado, ultima_voz_at, marca_bot
            while True:
                paquete = await ws.receive()
                if paquete.get("type") == "websocket.disconnect":
                    return
                if paquete.get("bytes") is not None:
                    if not pausado and _hay_voz(paquete["bytes"]):
                        ultima_voz_at = perf_counter()
                    grabador.escribir_cliente(paquete["bytes"], sample_rate)
                    await sesion.enviar_audio(silencio if pausado else paquete["bytes"])
                elif paquete.get("text"):
                    orden = json.loads(paquete["text"])
                    if orden.get("type") == "fin_turno":
                        await sesion.forzar_fin_turno()
                    elif orden.get("type") == "entrega":
                        if marca_bot is not None:
                            # `texto_entregado` ya descuenta lo que cortó un barge-in
                            turnos_bot.append(
                                (marca_bot, str(orden.get("texto_entregado") or texto_bot))
                            )
                            marca_bot = None
                        gestor.registrar_entrega(
                            llamada_id,
                            int(orden.get("turno", 0)),
                            str(orden.get("estado", "completada")),
                            float(orden.get("ratio", 1.0)),
                            orden.get("texto_entregado"),
                        )
                    elif orden.get("type") == "pausa":
                        if marca_bot is not None and texto_bot:
                            turnos_bot.append((marca_bot, texto_bot))  # sin `entrega` previa
                        marca_bot = grabador.marcar()
                        pausado = True
                    elif orden.get("type") == "reanudar":
                        pausado = False

        tareas = {
            asyncio.create_task(bombear_eventos(), name="eventos"),
            asyncio.create_task(recibir_audio(), name="audio"),
        }
        try:
            hechas, pendientes = await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
            for t in hechas:
                logger.warning(
                    "WebSocket de voz: tarea %s terminó (cierre STT=%s)",
                    t.get_name(),
                    getattr(sesion, "cierre", None),
                )
                exc = t.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    logger.exception(
                        "Fallo en la tarea %s del WebSocket de voz", t.get_name(), exc_info=exc
                    )
                    try:
                        await ws.send_json({"type": "error", "mensaje": f"{t.get_name()}: {exc}"})
                    except Exception:  # noqa: BLE001
                        pass
            for t in pendientes:
                t.cancel()
        finally:
            deps.descartar_especulaciones(llamada_id)
            await sesion.cerrar()
            if marca_bot is not None and texto_bot:
                turnos_bot.append((marca_bot, texto_bot))
            try:
                await reconstruir_voz_bot()
            except Exception:  # noqa: BLE001
                # Sin voz del bot la llamada se guarda igual: mejor una pista que ninguna
                logger.exception("No se pudo reconstruir la voz del bot en %s", llamada_id)
            try:
                await almacen_audio.guardar_conversacion(deps.repo, llamada_id, grabador)
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo guardar el audio de la llamada %s", llamada_id)
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    @app.post("/api/llamadas/{llamada_id}/entrega", dependencies=llamada_protegida)
    async def entrega(llamada_id: str, cuerpo: Entrega) -> dict[str, Any]:
        try:
            estado = gestor.registrar_entrega(
                llamada_id, cuerpo.turno, cuerpo.estado, cuerpo.ratio, cuerpo.texto_entregado
            )
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e
        return {"entrega_ultima": estado.get("entrega_ultima")}

    @app.post("/api/voz-bot", dependencies=protegido)
    async def voz_bot(cuerpo: VozBot) -> StreamingResponse:
        """La consola habla con la misma voz que el teléfono: PCM16 mono por trozos (`X-Tasa`)."""
        if not tts.disponible():
            raise HTTPException(503, "No hay voz de servidor configurada")
        trozos = fragmentos(tts, cuerpo.texto)
        # El primer trozo se pide antes de responder: un fallo del proveedor es un 502 limpio
        # (la consola vuelve a la voz del navegador) y no un audio cortado a medias
        try:
            primero = await anext(trozos, b"")
        except Exception as e:  # noqa: BLE001
            logger.warning("Voz del bot no disponible: %s", e)
            raise HTTPException(502, "La voz del servidor no responde") from e

        async def audio() -> Any:
            yield primero
            async for trozo in trozos:
                yield trozo

        return StreamingResponse(
            audio(),
            media_type="application/octet-stream",
            headers={"X-Tasa": str(tts.tasa), "Cache-Control": "no-store"},
        )

    @app.post("/api/llamadas/{llamada_id}/metricas", dependencies=llamada_protegida)
    async def metricas_cliente(llamada_id: str, cuerpo: MetricasCliente) -> dict[str, Any]:
        t = gestor.metricas.completar_cliente(llamada_id, cuerpo.turno, cuerpo.model_dump())
        if t is None:
            raise HTTPException(404, "Turno no encontrado")
        return t.como_dict()

    @app.get("/api/llamadas/{llamada_id}", dependencies=llamada_protegida)
    async def obtener(llamada_id: str) -> dict[str, Any]:
        try:
            return _vista(gestor.obtener(llamada_id))
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.get("/api/llamadas/{llamada_id}/metricas", dependencies=protegido)
    async def metricas_llamada(llamada_id: str) -> dict[str, Any]:
        return {
            "agregados": gestor.metricas.agregados(llamada_id),
            "turnos": [t.como_dict() for t in gestor.metricas.turnos(llamada_id)],
        }

    @app.get("/api/metricas", dependencies=protegido)
    async def metricas() -> dict[str, Any]:
        salida: dict[str, Any] = {
            "agregados": gestor.metricas.agregados(),
            "ultimos_turnos": [t.como_dict() for t in gestor.metricas.turnos()[-50:]],
        }
        if isinstance(deps.llm, ModeloCarrera):
            salida["carrera_ganadores"] = deps.llm.ganadores
        return salida

    @app.get("/api/incidencias", dependencies=protegido)
    async def incidencias() -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in await deps.repo.listar_incidencias()]

    return app


app = crear_app()


def run() -> None:
    import uvicorn

    uvicorn.run("building_call.api.main:app", host="127.0.0.1", port=8000, reload=True)
