"""API HTTP consumida por la consola de validación (y, más adelante, por la telefonía)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from building_call.graph.estado import EstadoLlamada
from building_call.graph.nodos import Dependencias
from building_call.graph.registro import REGISTRO
from building_call.llm.ollama import ModeloOllama
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas, LlamadaNoEncontrada
from building_call.settings import settings


class NuevaLlamada(BaseModel):
    telefono_llamante: str | None = None


class Mensaje(BaseModel):
    texto: str


def _vista(estado: EstadoLlamada) -> dict[str, Any]:
    campos = [
        "finca_id",
        "finca_direccion",
        "vivienda_id",
        "vivienda_etiqueta",
        "nombre_declarado",
        "solicitud",
        "categoria",
        "urgencia",
        "urgencia_declarada",
        "resumen",
        "confirmado",
        "especialista_id",
        "especialista_nombre",
        "canal_aviso",
        "destino_aviso",
        "incidencia_id",
        "resultado",
        "motivo_revision",
        "configuracion_id",
        "grafo",
        "intentos_finca",
        "intentos_vivienda",
    ]
    return {
        "llamada_id": estado["llamada_id"],
        "nodo_actual": estado.get("nodo_actual"),
        "esperando_usuario": estado.get("esperando_usuario", False),
        "finalizada": estado.get("finalizada", False),
        "mensajes_bot": estado.get("mensajes_bot", []),
        "historial": estado.get("historial", []),
        "traza": estado.get("traza", []),
        "validaciones": estado.get("validaciones", []),
        "senales_confianza": estado.get("senales_confianza"),
        "configuracion": estado.get("configuracion"),
        "datos": {c: estado.get(c) for c in campos},
    }


def crear_app(deps: Dependencias | None = None) -> FastAPI:
    if deps is None:
        if settings.repositorio == "postgres":
            from building_call.repos.postgres import RepositorioPostgres

            repo: Any = RepositorioPostgres(settings.database_url)
        else:
            repo = RepositorioMemoria()
        llm = ModeloOllama(settings.ollama_url, settings.ollama_modelo, settings.ollama_timeout_s)
        deps = Dependencias(repo=repo, llm=llm)

    gestor = GestorLlamadas(deps)
    app = FastAPI(title="Building Call Assistant", version="0.1.0")
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
            "grafos": sorted(REGISTRO),
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

    @app.get("/api/llamadas/{llamada_id}")
    async def obtener(llamada_id: str) -> dict[str, Any]:
        try:
            return _vista(gestor.obtener(llamada_id))
        except LlamadaNoEncontrada as e:
            raise HTTPException(404, "Llamada no encontrada") from e

    @app.get("/api/incidencias")
    async def incidencias() -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in await deps.repo.listar_incidencias()]

    return app


app = crear_app()


def run() -> None:
    import uvicorn

    uvicorn.run("building_call.api.main:app", host="127.0.0.1", port=8000, reload=True)
