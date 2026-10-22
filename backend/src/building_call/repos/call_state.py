"""Almacén del estado vivo de las llamadas en curso.

`CallManager` lee el estado al empezar cada turno y lo escribe al terminarlo: una lectura y una
escritura por turno como mucho. Con el almacén en PostgreSQL una llamada sobrevive a un reinicio
de la API y la puede continuar otra instancia; el de memoria es el de desarrollo y tests.

La retención es la misma en los dos: un estado se borra cuando lleva `finished_retention_s` sin
tocarse y la llamada terminó, o `abandoned_retention_s` sin tocarse en cualquier caso. Los
instantes (`now`) son segundos de reloj de pared (`time.time()`): se comparan entre procesos.

Leer y escribir no son atómicos (entre medias otro turno, otra petición u otra instancia puede
guardar), así que `save` no pisa un estado más nuevo: se ignora si trae un turno anterior al
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

from building_call.graph.state import CallState
from building_call.repos.sql_models import CallStateSQL


class CallStateStore(Protocol):
    purge_interval_s: float
    """Cada cuánto como mínimo merece la pena purgar (0 = en cada escritura)."""

    async def save(self, state: CallState, now: float) -> bool:
        """Guarda el estado salvo que pise uno más nuevo (ver arriba). Devuelve si lo guardó."""
        ...

    async def get(self, call_id: str) -> CallState | None: ...

    async def discard(self, call_id: str) -> None: ...

    async def purge(
        self, now: float, finished_retention_s: float, abandoned_retention_s: float
    ) -> list[str]:
        """Borra los estados caducados y devuelve sus `call_id`."""
        ...


class MemoryStateStore:
    """Diccionario del proceso (el comportamiento de siempre).

    `serializar=True` guarda el estado como JSON, igual que PostgreSQL: los tests lo usan para
    comprobar que lo que se reanuda tras un reinicio es exactamente lo que habría en la base."""

    purge_interval_s = 0.0

    def __init__(self, serialize: bool = False) -> None:
        self.serialize = serialize
        self._states: dict[str, Any] = {}
        self._turns: dict[str, int] = {}
        self._finished_calls: dict[str, bool] = {}
        self._last_use: dict[str, float] = {}

    async def save(self, state: CallState, now: float) -> bool:
        call_id = state["call_id"]
        turn, finished = int(state.get("turn", 0)), bool(state.get("finished"))
        if call_id in self._states and (
            turn < self._turns[call_id] or (self._finished(call_id) and not finished)
        ):
            return False
        self._states[call_id] = json.dumps(state) if self.serialize else state
        self._turns[call_id] = turn
        self._finished_calls[call_id] = finished
        self._last_use[call_id] = now
        return True

    async def get(self, call_id: str) -> CallState | None:
        saved = self._states.get(call_id)
        if saved is None or not self.serialize:
            return saved
        return cast(CallState, json.loads(saved))

    async def discard(self, call_id: str) -> None:
        self._states.pop(call_id, None)
        self._turns.pop(call_id, None)
        self._finished_calls.pop(call_id, None)
        self._last_use.pop(call_id, None)

    def _finished(self, call_id: str) -> bool:
        saved = self._states[call_id]
        # Sin serializar se mira el propio estado: puede haber cambiado en sitio desde que se
        # guardó (así se comportaba el diccionario de `CallManager`)
        if not self.serialize:
            return bool(saved.get("finished"))
        return self._finished_calls[call_id]

    async def purge(
        self, now: float, finished_retention_s: float, abandoned_retention_s: float
    ) -> list[str]:
        expired_calls = [
            call_id
            for call_id, usage in self._last_use.items()
            if now - usage > abandoned_retention_s
            or (now - usage > finished_retention_s and self._finished(call_id))
        ]
        for call_id in expired_calls:
            await self.discard(call_id)
        return expired_calls


def _instant(now: float) -> datetime:
    return datetime.fromtimestamp(now, UTC)


def save_statement(state: CallState, now: float) -> ReturningInsert[tuple[str]]:
    """Upsert de la fila de la llamada: la versión sube en cada escritura. No pisa un estado más
    nuevo (turno posterior, o llamada ya terminada); devuelve la fila solo si escribió."""
    table = CallStateSQL
    values = {
        "call_id": state["call_id"],
        "state": dict(state),
        "turn": int(state.get("turn", 0)),
        "version": 1,
        "finished": bool(state.get("finished")),
        "updated_at": _instant(now),
    }
    statement = insert(table).values(**values)
    excluded = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=[table.call_id],
        where=and_(
            excluded.turn >= table.turn,
            or_(excluded.finished.is_(True), not_(table.finished)),
        ),
        set_={
            "state": statement.excluded.state,
            "turn": statement.excluded.turn,
            "finished": statement.excluded.finished,
            "updated_at": statement.excluded.updated_at,
            "version": table.version + 1,
        },
    ).returning(table.call_id)


def purge_statement(
    now: float, finished_retention_s: float, abandoned_retention_s: float
) -> ReturningDelete[tuple[str]]:
    table = CallStateSQL
    return (
        delete(table)
        .where(
            or_(
                table.updated_at < _instant(now - abandoned_retention_s),
                and_(
                    table.finished.is_(True),
                    table.updated_at < _instant(now - finished_retention_s),
                ),
            )
        )
        .returning(table.call_id)
    )


class PostgresStateStore:
    """Tabla `call_states` (migración 0008). Una consulta por operación, por clave primaria.

    La purga es un DELETE por índice de `updated_at`; con una por minuto basta (la retención
    se mide en cuartos de hora) y no se suma una consulta a cada turno."""

    purge_interval_s = 60.0

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def save(self, state: CallState, now: float) -> bool:
        async with self.sessions() as s:
            row = (await s.execute(save_statement(state, now))).scalar_one_or_none()
            await s.commit()
        return row is not None

    async def get(self, call_id: str) -> CallState | None:
        table = CallStateSQL
        async with self.sessions() as s:
            row = (
                await s.execute(select(table.state).where(table.call_id == call_id))
            ).scalar_one_or_none()
        return cast(CallState, row) if row is not None else None

    async def discard(self, call_id: str) -> None:
        table = CallStateSQL
        async with self.sessions() as s:
            await s.execute(delete(table).where(table.call_id == call_id))
            await s.commit()

    async def purge(
        self, now: float, finished_retention_s: float, abandoned_retention_s: float
    ) -> list[str]:
        async with self.sessions() as s:
            rows = await s.execute(
                purge_statement(now, finished_retention_s, abandoned_retention_s)
            )
            await s.commit()
            return list(rows.scalars())
