"""API HTTP/WebSocket consumida por la consola de validación (y, más adelante, por la telefonía)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from building_call.graph.estado import EstadoLlamada
from building_call.graph.nodos import Dependencias
from building_call.graph.registro import REGISTRO
from building_call.llm.carrera import ModeloCarrera
from building_call.llm.ollama import ModeloOllama
from building_call.llm.workers_ai import ModeloWorkersAI
from building_call.metricas import UMBRALES_MS
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas, LlamadaNoEncontrada
from building_call.settings import settings
from building_call.stt import ReconocedorNoDisponible, ReconocedorVoz, ReconocedorWhisper
from building_call.stt_streaming import SesionStreamingAssemblyAI, validar_endpoint_eu

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


def crear_app(
    deps: Dependencias | None = None,
    stt: ReconocedorVoz | None = None,
    fabrica_streaming: FabricaStreaming | None | Any = AUTO,
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
    redactor = None
    if settings.redaccion == "llm":
        # Redacción con el modelo local y salida corta: la carrera con el 27B tardaba 1,5 s aquí
        redactor = ModeloOllama(
            settings.ollama_url, settings.ollama_modelo, settings.ollama_timeout_s, num_predict=90
        )
    gestor = GestorLlamadas(deps, redactor=redactor)

    @asynccontextmanager
    async def ciclo_vida(_: FastAPI):  # type: ignore[no-untyped-def]
        # Calienta modelo y STT para que el primer turno real no pague la carga
        if hasattr(deps.llm, "calentar"):
            await deps.llm.calentar()
        if hasattr(reconocedor, "calentar"):
            await reconocedor.calentar()
        yield

    app = FastAPI(title="Building Call Assistant", version="0.3.0", lifespan=ciclo_vida)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
            "grafos": sorted(REGISTRO),
            "umbrales_ms": {nombre: limite for limite, nombre in UMBRALES_MS},
        }

    @app.get("/api/fincas")
    async def fincas() -> list[dict[str, Any]]:
        salida = []
        for f in await deps.repo.listar_fincas():
            viviendas = await deps.repo.viviendas_de(f.id)
            salida.append({**f.model_dump(), "viviendas": [v.etiqueta() for v in viviendas]})
        return salida

    @app.post("/api/llamadas", status_code=201)
    async def iniciar(cuerpo: NuevaLlamada) -> dict[str, Any]:
        return _vista(await gestor.iniciar(cuerpo.telefono_llamante))

    @app.post("/api/llamadas/{llamada_id}/mensajes")
    async def mensaje(llamada_id: str, cuerpo: Mensaje) -> dict[str, Any]:
        try:
            return _vista(await gestor.mensaje(llamada_id, cuerpo.texto))
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.post("/api/llamadas/{llamada_id}/audio")
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
        if fabrica_streaming is None:
            await ws.send_json({"type": "error", "mensaje": "STT en streaming no configurado"})
            await ws.close(code=1011)
            return
        try:
            gestor.obtener(llamada_id)
        except LlamadaNoEncontrada:
            await ws.send_json({"type": "error", "mensaje": "Llamada no encontrada"})
            await ws.close(code=1008)
            return
        sample_rate = int(ws.query_params.get("sample_rate", "16000"))
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

        async def bombear_eventos() -> None:
            async for ev in sesion.eventos():
                if ev.tipo == "parcial":
                    await ws.send_json({"type": "parcial", "texto": ev.texto})
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
                    await ws.send_json({"type": "respuesta", **vista})
                    if estado.get("finalizada"):
                        return

        async def recibir_audio() -> None:
            nonlocal pausado, ultima_voz_at
            while True:
                paquete = await ws.receive()
                if paquete.get("type") == "websocket.disconnect":
                    return
                if paquete.get("bytes") is not None:
                    if not pausado and _hay_voz(paquete["bytes"]):
                        ultima_voz_at = perf_counter()
                    await sesion.enviar_audio(silencio if pausado else paquete["bytes"])
                elif paquete.get("text"):
                    orden = json.loads(paquete["text"])
                    if orden.get("type") == "fin_turno":
                        await sesion.forzar_fin_turno()
                    elif orden.get("type") == "entrega":
                        gestor.registrar_entrega(
                            llamada_id,
                            int(orden.get("turno", 0)),
                            str(orden.get("estado", "completada")),
                            float(orden.get("ratio", 1.0)),
                            orden.get("texto_entregado"),
                        )
                    elif orden.get("type") == "pausa":
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
            await sesion.cerrar()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    @app.post("/api/llamadas/{llamada_id}/entrega")
    async def entrega(llamada_id: str, cuerpo: Entrega) -> dict[str, Any]:
        try:
            estado = gestor.registrar_entrega(
                llamada_id, cuerpo.turno, cuerpo.estado, cuerpo.ratio, cuerpo.texto_entregado
            )
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e
        return {"entrega_ultima": estado.get("entrega_ultima")}

    @app.post("/api/llamadas/{llamada_id}/metricas")
    async def metricas_cliente(llamada_id: str, cuerpo: MetricasCliente) -> dict[str, Any]:
        t = gestor.metricas.completar_cliente(llamada_id, cuerpo.turno, cuerpo.model_dump())
        if t is None:
            raise HTTPException(404, "Turno no encontrado")
        return t.como_dict()

    @app.get("/api/llamadas/{llamada_id}")
    async def obtener(llamada_id: str) -> dict[str, Any]:
        try:
            return _vista(gestor.obtener(llamada_id))
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.get("/api/llamadas/{llamada_id}/metricas")
    async def metricas_llamada(llamada_id: str) -> dict[str, Any]:
        return {
            "agregados": gestor.metricas.agregados(llamada_id),
            "turnos": [t.como_dict() for t in gestor.metricas.turnos(llamada_id)],
        }

    @app.get("/api/metricas")
    async def metricas() -> dict[str, Any]:
        salida: dict[str, Any] = {
            "agregados": gestor.metricas.agregados(),
            "ultimos_turnos": [t.como_dict() for t in gestor.metricas.turnos()[-50:]],
        }
        if isinstance(deps.llm, ModeloCarrera):
            salida["carrera_ganadores"] = deps.llm.ganadores
        return salida

    @app.get("/api/incidencias")
    async def incidencias() -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in await deps.repo.listar_incidencias()]

    return app


app = crear_app()


def run() -> None:
    import uvicorn

    uvicorn.run("building_call.api.main:app", host="127.0.0.1", port=8000, reload=True)
