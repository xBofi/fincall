"""API HTTP consumida por la consola de validación (y, más adelante, por la telefonía)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from building_call.graph.estado import EstadoLlamada
from building_call.graph.nodos import Dependencias
from building_call.graph.registro import REGISTRO
from building_call.llm.ollama import ModeloOllama
from building_call.metricas import UMBRALES_MS
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas, LlamadaNoEncontrada
from building_call.settings import settings
from building_call.stt import ReconocedorNoDisponible, ReconocedorVoz, ReconocedorWhisper


class NuevaLlamada(BaseModel):
    telefono_llamante: str | None = None


class Mensaje(BaseModel):
    texto: str


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
        "tiempos": estado.get("tiempos_turno", {}),
        "configuracion": estado.get("configuracion"),
        "datos": {c: estado.get(c) for c in campos},
    }


def crear_app(deps: Dependencias | None = None, stt: ReconocedorVoz | None = None) -> FastAPI:
    if deps is None:
        if settings.repositorio == "postgres":
            from building_call.repos.postgres import RepositorioPostgres

            repo: Any = RepositorioPostgres(settings.database_url)
        else:
            repo = RepositorioMemoria()
        llm = ModeloOllama(
            settings.ollama_url,
            settings.ollama_modelo,
            settings.ollama_timeout_s,
            num_predict=settings.ollama_num_predict,
            num_ctx=settings.ollama_num_ctx,
        )
        deps = Dependencias(repo=repo, llm=llm)
    if stt is None:
        stt = (
            ReconocedorWhisper(
                settings.whisper_modelo, settings.whisper_dispositivo, settings.whisper_computo
            )
            if settings.stt == "whisper"
            else ReconocedorNoDisponible()
        )
    reconocedor: ReconocedorVoz = stt
    gestor = GestorLlamadas(deps)

    @asynccontextmanager
    async def ciclo_vida(_: FastAPI):  # type: ignore[no-untyped-def]
        # Calienta modelo y STT para que el primer turno real no pague la carga
        if hasattr(deps.llm, "calentar"):
            await deps.llm.calentar()
        if hasattr(reconocedor, "calentar"):
            await reconocedor.calentar()
        yield

    app = FastAPI(title="Building Call Assistant", version="0.2.0", lifespan=ciclo_vida)
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
        """Turno por voz: el servidor transcribe (faster-whisper) y ejecuta el grafo."""
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
        return {
            "agregados": gestor.metricas.agregados(),
            "ultimos_turnos": [t.como_dict() for t in gestor.metricas.turnos()[-50:]],
        }

    @app.get("/api/incidencias")
    async def incidencias() -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in await deps.repo.listar_incidencias()]

    return app


app = crear_app()


def run() -> None:
    import uvicorn

    uvicorn.run("building_call.api.main:app", host="127.0.0.1", port=8000, reload=True)
