"""Gestor de sesiones de llamada: mantiene el estado entre turnos, invoca el grafo y mide."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

from building_call.domain.entidades import Llamada
from building_call.graph.estado import EstadoLlamada, estado_inicial
from building_call.graph.ledger import avisos_no_oidos, evaluar, trocear
from building_call.graph.nodos import Dependencias
from building_call.graph.registro import obtener_grafo
from building_call.llm.redaccion import redactar
from building_call.metricas import RegistroMetricas, TiemposTurno


class LlamadaNoEncontrada(KeyError):
    pass


def _datos_para_redaccion(estado: EstadoLlamada) -> dict[str, Any]:
    return {
        "finca": estado.get("finca_direccion"),
        "vivienda": estado.get("vivienda_etiqueta"),
        "nombre": estado.get("nombre_declarado"),
        "problema": estado.get("resumen") or estado.get("solicitud"),
        "categoria": estado.get("categoria"),
        "urgencia": estado.get("urgencia"),
        "nodo": estado.get("nodo_actual"),
    }


def _merece_redaccion(estado: EstadoLlamada, entrada: str | None) -> bool:
    """Redactar cuesta una llamada LLM (~0,5-1 s). Solo cuando aporta naturalidad: el vecino ha
    dicho algo que no era la respuesta esperada (turno interpretado por LLM o intención global)
    y el mensaje no es un cierre largo lleno de hechos (ahí la plantilla ya es completa)."""
    if entrada is None or estado.get("finalizada"):
        return False
    tiempos = estado.get("tiempos_turno", {})
    if tiempos.get("origen_interpretacion") != "llm":
        return False
    return len(" ".join(estado.get("mensajes_bot", []))) <= 260


def _anteponer_avisos(nuevo: EstadoLlamada, avisos: list[str]) -> list[str]:
    """Los avisos que el vecino cortó en el turno anterior van delante de la respuesta, una vez.
    Devuelve los que se han antepuesto."""
    dicho = " ".join(nuevo.get("mensajes_bot", []))
    faltan = [a for a in avisos if a not in dicho]
    if not faltan or not nuevo.get("mensajes_bot"):
        return []
    primero = nuevo["mensajes_bot"][0]
    con_avisos = " ".join([*faltan, primero])
    nuevo["mensajes_bot"] = [con_avisos, *nuevo["mensajes_bot"][1:]]
    historial = list(nuevo.get("historial", []))
    for i in range(len(historial) - 1, -1, -1):
        if historial[i]["rol"] == "bot" and historial[i]["texto"] == primero:
            historial[i] = {"rol": "bot", "texto": con_avisos}
            break
    nuevo["historial"] = historial
    return faltan


class GestorLlamadas:
    # El expediente se persiste en cada turno; el estado vivo solo hace falta mientras la llamada
    # sigue (y un margen para las últimas entregas de audio de la consola)
    RETENCION_FINALIZADA_S = 15 * 60
    RETENCION_ABANDONADA_S = 2 * 60 * 60

    def __init__(
        self,
        deps: Dependencias,
        grafo_por_defecto: str = "estandar",
        metricas: RegistroMetricas | None = None,
        redactor: Any | None = None,
        reloj: Callable[[], float] = monotonic,
    ) -> None:
        """`redactor`: modelo que reformula las respuestas del grafo (paráfrasis controlada).
        None = plantillas tal cual."""
        self.deps = deps
        self.redactor = redactor
        self.grafo_por_defecto = grafo_por_defecto
        self.metricas = metricas or RegistroMetricas()
        self._grafos: dict[str, Any] = {}
        self._estados: dict[str, EstadoLlamada] = {}
        self._ultimo_uso: dict[str, float] = {}
        self._reloj = reloj

    def _guardar_estado(self, estado: EstadoLlamada) -> None:
        ahora = self._reloj()
        self._estados[estado["llamada_id"]] = estado
        self._ultimo_uso[estado["llamada_id"]] = ahora
        for llamada_id, uso in list(self._ultimo_uso.items()):
            inactiva = ahora - uso
            if inactiva > self.RETENCION_ABANDONADA_S or (
                inactiva > self.RETENCION_FINALIZADA_S
                and self._estados[llamada_id].get("finalizada")
            ):
                del self._estados[llamada_id], self._ultimo_uso[llamada_id]
                self.deps.descartar_especulaciones(llamada_id)

    def _grafo(self, nombre: str) -> Any:
        if nombre not in self._grafos:
            self._grafos[nombre] = obtener_grafo(nombre, self.deps)
        return self._grafos[nombre]

    async def _guardar_operacion(self, estado: EstadoLlamada) -> None:
        """Persiste el expediente tras cada turno, también mientras sigue activo."""
        finalizada = bool(estado.get("finalizada"))
        resultado = estado.get("resultado")
        await self.deps.repo.guardar_llamada(
            Llamada(
                id=estado["llamada_id"],
                iniciada_en=datetime.fromisoformat(estado["iniciada_en"]),
                telefono_llamante=estado.get("telefono_llamante"),
                finca_id=estado.get("finca_id"),
                vivienda_id=estado.get("vivienda_id"),
                nombre_declarado=estado.get("nombre_declarado"),
                transcripcion=estado.get("historial", []),
                resumen=estado.get("resumen") or estado.get("solicitud"),
                resultado=resultado,
                motivo_revision=estado.get("motivo_revision"),
                configuracion_id=estado.get("configuracion_id"),
                grafo=estado.get("grafo"),
                estado="finalizada" if finalizada else "activa",
                finalizada_en=datetime.now(UTC) if finalizada else None,
                requiere_revision=finalizada and resultado != "incidencia_creada",
            )
        )

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
            "avisos_turno": [],
        }
        t0 = perf_counter()
        nuevo: EstadoLlamada = await self._grafo(nombre_grafo).ainvoke(preparado)
        tiempos = dict(nuevo.get("tiempos_turno", {}))
        if (
            self.redactor is not None
            and nuevo.get("mensajes_bot")
            and _merece_redaccion(nuevo, entrada)
        ):
            plantilla = " ".join(nuevo["mensajes_bot"])
            texto, metrica = await redactar(
                self.redactor, plantilla, entrada, _datos_para_redaccion(nuevo)
            )
            tiempos.update(metrica)
            if texto != plantilla:
                nuevo["mensajes_bot"] = [texto]
                # El historial guarda lo que realmente se dijo
                # Los mensajes del grafo de este turno son los últimos del historial: se
                # sustituyen por la redacción que realmente se dijo
                historial = list(nuevo.get("historial", []))
                while (
                    historial
                    and historial[-1]["rol"] == "bot"
                    and historial[-1]["texto"] in plantilla
                ):
                    historial.pop()
                historial.append({"rol": "bot", "texto": texto})
                nuevo["historial"] = historial
        grafo_ms = int((perf_counter() - t0) * 1000)
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
        avisos = list(nuevo.get("avisos_turno", []))
        if entrada is not None:
            avisos += _anteponer_avisos(nuevo, avisos_no_oidos(estado.get("entrega_ultima")))
        if nuevo.get("mensajes_bot"):
            nuevo["ultimo_mensaje_bot"] = " ".join(nuevo["mensajes_bot"])
            nuevo["piezas_bot"] = [
                dict(p) for p in trocear(" ".join(nuevo["mensajes_bot"]), avisos)
            ]
            nuevo["entrega_ultima"] = None  # el navegador informará de lo que llegó a sonar
        tiempos.update(grafo_ms=grafo_ms, servidor_ms=servidor_ms, turno=turno)
        if stt_ms is not None:
            tiempos["stt_ms"] = stt_ms
        nuevo["tiempos_turno"] = tiempos
        self._guardar_estado(nuevo)
        await self._guardar_operacion(nuevo)
        await self._guardar_trazabilidad(estado, nuevo, entrada, tiempos)
        return nuevo

    async def _guardar_trazabilidad(
        self,
        anterior: EstadoLlamada,
        nuevo: EstadoLlamada,
        entrada: str | None,
        tiempos: dict[str, Any],
    ) -> None:
        """Turno (qué dijo cada parte, cómo se interpretó, cuánto tardó) y decisiones del grafo
        (las entradas de traza nuevas en este turno), para el dossier de la consola operativa."""
        repo: Any = self.deps.repo
        if not hasattr(repo, "guardar_turno"):
            return
        turno = nuevo.get("turno", 0)
        await repo.guardar_turno(
            nuevo["llamada_id"],
            turno,
            {
                "vecino": entrada,
                "asistente": nuevo.get("mensajes_bot", []),
                "nodo_inicial": anterior.get("nodo_actual"),
                "nodo_final": nuevo.get("nodo_actual"),
                "interpretacion": nuevo.get("interpretacion"),
                "tiempos": tiempos,
            },
        )
        previas = len(anterior.get("traza", []))
        for paso in nuevo.get("traza", [])[previas:]:
            await repo.guardar_decision(
                nuevo["llamada_id"],
                turno,
                paso["nodo"],
                {"accion": paso["accion"], "detalle": paso["detalle"]},
            )

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

    async def cerrar_por_fallo(self, llamada_id: str, motivo: str) -> EstadoLlamada:
        """El asistente no puede seguir (LLM, STT o TTS caídos): se cierra la llamada y queda
        derivada al administrador y marcada para revisar, en vez de quedarse "activa"."""
        estado = self.obtener(llamada_id)
        if not estado.get("finalizada"):
            estado = {
                **estado,
                "finalizada": True,
                "resultado": "derivada_administrador",
                "motivo_revision": motivo,
            }
            self._guardar_estado(estado)
            await self._guardar_operacion(estado)
        return estado

    def registrar_entrega(
        self, llamada_id: str, turno: int, estado_entrega: str, ratio: float, texto: str | None
    ) -> EstadoLlamada:
        """Ledger de salida: qué parte del último mensaje llegó a reproducirse al vecino."""
        estado = self.obtener(llamada_id)
        if turno != estado.get("turno", 0):
            return estado  # entrega de un turno ya superado: no altera el estado actual
        mensaje = estado.get("ultimo_mensaje_bot") or ""
        corte: int | None
        if estado_entrega == "completada":
            corte = len(mensaje)
        elif estado_entrega == "no_reproducida":
            corte = 0
        else:
            # Sin texto no hay medida (Twilio corta y anota un 0,5 fijo): sin piezas evaluadas
            # el turno siguiente se comporta como antes del ledger, en vez de decidir sobre una
            # posición inventada
            corte = len(texto) if texto is not None else None
        piezas = evaluar(estado.get("piezas_bot", []), corte) if corte is not None else []  # type: ignore[arg-type]
        estado["entrega_ultima"] = {
            "estado": estado_entrega,
            "ratio": max(0.0, min(1.0, ratio)),
            "texto_entregado": texto,
            "piezas": [dict(p) for p in piezas],
        }
        self._guardar_estado(estado)
        return estado

    def obtener(self, llamada_id: str) -> EstadoLlamada:
        try:
            return self._estados[llamada_id]
        except KeyError as e:
            raise LlamadaNoEncontrada(llamada_id) from e
