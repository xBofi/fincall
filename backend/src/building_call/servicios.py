"""Gestor de sesiones de llamada: mantiene el estado entre turnos, invoca el grafo y mide."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from building_call.graph.estado import EstadoLlamada, estado_inicial
from building_call.graph.nodos import Dependencias
from building_call.graph.registro import obtener_grafo
from building_call.metricas import RegistroMetricas, TiemposTurno


class LlamadaNoEncontrada(KeyError):
    pass


class GestorLlamadas:
    def __init__(
        self,
        deps: Dependencias,
        grafo_por_defecto: str = "estandar",
        metricas: RegistroMetricas | None = None,
    ) -> None:
        self.deps = deps
        self.grafo_por_defecto = grafo_por_defecto
        self.metricas = metricas or RegistroMetricas()
        self._grafos: dict[str, Any] = {}
        self._estados: dict[str, EstadoLlamada] = {}

    def _grafo(self, nombre: str) -> Any:
        if nombre not in self._grafos:
            self._grafos[nombre] = obtener_grafo(nombre, self.deps)
        return self._grafos[nombre]

    async def _ejecutar(
        self, estado: EstadoLlamada, entrada: str | None, stt_ms: int | None = None
    ) -> EstadoLlamada:
        # El grafo a usar puede cambiar al resolver la configuración de la finca
        nombre_grafo = (
            (estado.get("configuracion") or {}).get("grafo")
            or estado.get("grafo")
            or self.grafo_por_defecto
        )
        turno = estado.get("turno", 0) + (1 if entrada is not None else 0)
        nodo_inicial = estado.get("nodo_actual", "siguiente_paso")
        preparado: EstadoLlamada = {
            **estado,
            "entrada_usuario": entrada,
            "mensajes_bot": [],
            "esperando_usuario": False,
            "grafo": nombre_grafo,
            "turno": turno,
            "tiempos_turno": {},
        }
        t0 = perf_counter()
        nuevo: EstadoLlamada = await self._grafo(nombre_grafo).ainvoke(preparado)
        grafo_ms = int((perf_counter() - t0) * 1000)
        tiempos = dict(nuevo.get("tiempos_turno", {}))
        servidor_ms = grafo_ms + (stt_ms or 0)
        self.metricas.registrar(
            TiemposTurno(
                llamada_id=nuevo["llamada_id"],
                turno=turno,
                nodo_inicial=nodo_inicial,
                nodo_final=nuevo.get("nodo_actual", ""),
                stt_ms=stt_ms,
                interpretacion_ms=tiempos.get("interpretacion_ms"),
                clasificacion_ms=tiempos.get("clasificacion_ms"),
                llm_llamadas=int(tiempos.get("llm_llamadas", 0)),
                grafo_ms=grafo_ms,
                servidor_ms=servidor_ms,
                origen_interpretacion=tiempos.get("origen_interpretacion", "ninguna"),
            )
        )
        tiempos.update(grafo_ms=grafo_ms, servidor_ms=servidor_ms, turno=turno)
        if stt_ms is not None:
            tiempos["stt_ms"] = stt_ms
        nuevo["tiempos_turno"] = tiempos
        self._estados[nuevo["llamada_id"]] = nuevo
        return nuevo

    async def iniciar(self, telefono_llamante: str | None = None) -> EstadoLlamada:
        llamada_id = f"llam-{uuid4().hex[:12]}"
        estado = estado_inicial(llamada_id, datetime.now(UTC).isoformat(), self.grafo_por_defecto)
        estado["telefono_llamante"] = telefono_llamante
        return await self._ejecutar(estado, None)

    async def mensaje(
        self, llamada_id: str, texto: str, stt_ms: int | None = None
    ) -> EstadoLlamada:
        estado = self.obtener(llamada_id)
        if estado.get("finalizada"):
            return estado
        return await self._ejecutar(estado, texto, stt_ms)

    def obtener(self, llamada_id: str) -> EstadoLlamada:
        try:
            return self._estados[llamada_id]
        except KeyError as e:
            raise LlamadaNoEncontrada(llamada_id) from e
