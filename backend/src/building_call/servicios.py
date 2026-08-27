"""Gestor de sesiones de llamada: mantiene el estado entre turnos e invoca el grafo."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from building_call.graph.estado import EstadoLlamada, estado_inicial
from building_call.graph.nodos import Dependencias
from building_call.graph.registro import obtener_grafo


class LlamadaNoEncontrada(KeyError):
    pass


class GestorLlamadas:
    def __init__(self, deps: Dependencias, grafo_por_defecto: str = "estandar") -> None:
        self.deps = deps
        self.grafo_por_defecto = grafo_por_defecto
        self._grafos: dict[str, Any] = {}
        self._estados: dict[str, EstadoLlamada] = {}

    def _grafo(self, nombre: str) -> Any:
        if nombre not in self._grafos:
            self._grafos[nombre] = obtener_grafo(nombre, self.deps)
        return self._grafos[nombre]

    async def _ejecutar(self, estado: EstadoLlamada, entrada: str | None) -> EstadoLlamada:
        # El grafo a usar puede cambiar al resolver la configuración de la finca
        nombre_grafo = (
            (estado.get("configuracion") or {}).get("grafo")
            or estado.get("grafo")
            or self.grafo_por_defecto
        )
        preparado: EstadoLlamada = {
            **estado,
            "entrada_usuario": entrada,
            "mensajes_bot": [],
            "esperando_usuario": False,
            "grafo": nombre_grafo,
        }
        nuevo: EstadoLlamada = await self._grafo(nombre_grafo).ainvoke(preparado)
        self._estados[nuevo["llamada_id"]] = nuevo
        return nuevo

    async def iniciar(self, telefono_llamante: str | None = None) -> EstadoLlamada:
        llamada_id = f"llam-{uuid4().hex[:12]}"
        estado = estado_inicial(llamada_id, datetime.now(UTC).isoformat(), self.grafo_por_defecto)
        estado["telefono_llamante"] = telefono_llamante
        return await self._ejecutar(estado, None)

    async def mensaje(self, llamada_id: str, texto: str) -> EstadoLlamada:
        estado = self.obtener(llamada_id)
        if estado.get("finalizada"):
            return estado
        return await self._ejecutar(estado, texto)

    def obtener(self, llamada_id: str) -> EstadoLlamada:
        try:
            return self._estados[llamada_id]
        except KeyError as e:
            raise LlamadaNoEncontrada(llamada_id) from e
