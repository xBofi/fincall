"""Almacén del estado vivo de las llamadas en curso.

`GestorLlamadas` lee el estado al empezar cada turno y lo escribe al terminarlo: una lectura y una
escritura por turno como mucho. Con el almacén en PostgreSQL una llamada sobrevive a un reinicio
de la API y la puede continuar otra instancia; el de memoria es el de desarrollo y tests.

La retención es la misma en los dos: un estado se borra cuando lleva `retencion_finalizada_s` sin
tocarse y la llamada terminó, o `retencion_abandonada_s` sin tocarse en cualquier caso. Los
instantes (`ahora`) son segundos de reloj de pared (`time.time()`): se comparan entre procesos.

Leer y escribir no son atómicos (entre medias otro turno, otra petición u otra instancia puede
guardar), así que `guardar` no pisa un estado más nuevo: se ignora si trae un turno anterior al
guardado o si reabre una llamada ya terminada, y devuelve si se guardó.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import and_, delete, not_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import ReturningDelete, ReturningInsert

from building_call.graph.estado import EstadoLlamada
from building_call.repos.modelos_sql import EstadoLlamadaSQL


class AlmacenEstadoLlamada(Protocol):
    intervalo_purga_s: float
    """Cada cuánto como mínimo merece la pena purgar (0 = en cada escritura)."""

    async def guardar(self, estado: EstadoLlamada, ahora: float) -> bool:
        """Guarda el estado salvo que pise uno más nuevo (ver arriba). Devuelve si lo guardó."""
        ...

    async def obtener(self, llamada_id: str) -> EstadoLlamada | None: ...

    async def descartar(self, llamada_id: str) -> None: ...

    async def purgar(
        self, ahora: float, retencion_finalizada_s: float, retencion_abandonada_s: float
    ) -> list[str]:
        """Borra los estados caducados y devuelve sus `llamada_id`."""
        ...


class AlmacenEstadoMemoria:
    """Diccionario del proceso (el comportamiento de siempre).

    `serializar=True` guarda el estado como JSON, igual que PostgreSQL: los tests lo usan para
    comprobar que lo que se reanuda tras un reinicio es exactamente lo que habría en la base."""

    intervalo_purga_s = 0.0

    def __init__(self, serializar: bool = False) -> None:
        self.serializar = serializar
        self._estados: dict[str, Any] = {}
        self._turnos: dict[str, int] = {}
        self._finalizadas: dict[str, bool] = {}
        self._ultimo_uso: dict[str, float] = {}

    async def guardar(self, estado: EstadoLlamada, ahora: float) -> bool:
        llamada_id = estado["llamada_id"]
        turno, finalizada = int(estado.get("turno", 0)), bool(estado.get("finalizada"))
        if llamada_id in self._estados and (
            turno < self._turnos[llamada_id] or (self._finalizada(llamada_id) and not finalizada)
        ):
            return False
        self._estados[llamada_id] = json.dumps(estado) if self.serializar else estado
        self._turnos[llamada_id] = turno
        self._finalizadas[llamada_id] = finalizada
        self._ultimo_uso[llamada_id] = ahora
        return True

    async def obtener(self, llamada_id: str) -> EstadoLlamada | None:
        guardado = self._estados.get(llamada_id)
        if guardado is None or not self.serializar:
            return guardado
        return cast(EstadoLlamada, json.loads(guardado))

    async def descartar(self, llamada_id: str) -> None:
        self._estados.pop(llamada_id, None)
        self._turnos.pop(llamada_id, None)
        self._finalizadas.pop(llamada_id, None)
        self._ultimo_uso.pop(llamada_id, None)

    def _finalizada(self, llamada_id: str) -> bool:
        guardado = self._estados[llamada_id]
        # Sin serializar se mira el propio estado: puede haber cambiado en sitio desde que se
        # guardó (así se comportaba el diccionario de `GestorLlamadas`)
        if not self.serializar:
            return bool(guardado.get("finalizada"))
        return self._finalizadas[llamada_id]

    async def purgar(
        self, ahora: float, retencion_finalizada_s: float, retencion_abandonada_s: float
    ) -> list[str]:
        caducadas = [
            llamada_id
            for llamada_id, uso in self._ultimo_uso.items()
            if ahora - uso > retencion_abandonada_s
            or (ahora - uso > retencion_finalizada_s and self._finalizada(llamada_id))
        ]
        for llamada_id in caducadas:
            await self.descartar(llamada_id)
        return caducadas


def _instante(ahora: float) -> datetime:
    return datetime.fromtimestamp(ahora, UTC)


def sentencia_guardar(estado: EstadoLlamada, ahora: float) -> ReturningInsert[tuple[str]]:
    """Upsert de la fila de la llamada: la versión sube en cada escritura. No pisa un estado más
    nuevo (turno posterior, o llamada ya terminada); devuelve la fila solo si escribió."""
    tabla = EstadoLlamadaSQL
    valores = {
        "llamada_id": estado["llamada_id"],
        "estado": dict(estado),
        "turno": int(estado.get("turno", 0)),
        "version": 1,
        "finalizada": bool(estado.get("finalizada")),
        "actualizada_en": _instante(ahora),
    }
    sentencia = insert(tabla).values(**valores)
    excluido = sentencia.excluded
    return sentencia.on_conflict_do_update(
        index_elements=[tabla.llamada_id],
        where=and_(
            excluido.turno >= tabla.turno,
            or_(excluido.finalizada.is_(True), not_(tabla.finalizada)),
        ),
        set_={
            "estado": sentencia.excluded.estado,
            "turno": sentencia.excluded.turno,
            "finalizada": sentencia.excluded.finalizada,
            "actualizada_en": sentencia.excluded.actualizada_en,
            "version": tabla.version + 1,
        },
    ).returning(tabla.llamada_id)


def sentencia_purgar(
    ahora: float, retencion_finalizada_s: float, retencion_abandonada_s: float
) -> ReturningDelete[tuple[str]]:
    tabla = EstadoLlamadaSQL
    return (
        delete(tabla)
        .where(
            or_(
                tabla.actualizada_en < _instante(ahora - retencion_abandonada_s),
                and_(
                    tabla.finalizada.is_(True),
                    tabla.actualizada_en < _instante(ahora - retencion_finalizada_s),
                ),
            )
        )
        .returning(tabla.llamada_id)
    )


class AlmacenEstadoPostgres:
    """Tabla `estados_llamada` (migración 0008). Una consulta por operación, por clave primaria.

    La purga es un DELETE por índice de `actualizada_en`; con una por minuto basta (la retención
    se mide en cuartos de hora) y no se suma una consulta a cada turno."""

    intervalo_purga_s = 60.0

    def __init__(self, sesiones: async_sessionmaker[AsyncSession]) -> None:
        self.sesiones = sesiones

    async def guardar(self, estado: EstadoLlamada, ahora: float) -> bool:
        async with self.sesiones() as s:
            fila = (await s.execute(sentencia_guardar(estado, ahora))).scalar_one_or_none()
            await s.commit()
        return fila is not None

    async def obtener(self, llamada_id: str) -> EstadoLlamada | None:
        tabla = EstadoLlamadaSQL
        async with self.sesiones() as s:
            fila = (
                await s.execute(select(tabla.estado).where(tabla.llamada_id == llamada_id))
            ).scalar_one_or_none()
        return cast(EstadoLlamada, fila) if fila is not None else None

    async def descartar(self, llamada_id: str) -> None:
        tabla = EstadoLlamadaSQL
        async with self.sesiones() as s:
            await s.execute(delete(tabla).where(tabla.llamada_id == llamada_id))
            await s.commit()

    async def purgar(
        self, ahora: float, retencion_finalizada_s: float, retencion_abandonada_s: float
    ) -> list[str]:
        async with self.sesiones() as s:
            filas = await s.execute(
                sentencia_purgar(ahora, retencion_finalizada_s, retencion_abandonada_s)
            )
            await s.commit()
            return list(filas.scalars())
