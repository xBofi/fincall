"""Carga los datos de demostración del repositorio en memoria en PostgreSQL.

Uso: `uv run python -m building_call.repos.seed` (requiere BC_DATABASE_URL y migraciones aplicadas).
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from building_call.audit import session as audit_session
from building_call.audit.context import AuditContext, audit_context
from building_call.repos.memory import MemoryRepository
from building_call.repos.sql_models import (
    AdministratorSQL,
    ConfigSQL,
    EquipmentSQL,
    EstateSQL,
    HomeSQL,
    SpecialistSQL,
)
from building_call.settings import settings


async def seed(database_url: str) -> None:
    demo = MemoryRepository()
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    audit_session.install()
    with audit_context(AuditContext(actor="system:seed")):
        await _seed(sessions, demo)
    await engine.dispose()


async def _seed(sessions: Any, demo: MemoryRepository) -> None:
    async with sessions() as s, s.begin():
        for a in demo.administrators.values():
            await s.merge(AdministratorSQL(**a.model_dump()))
        for e in demo.specialists.values():
            await s.merge(SpecialistSQL(**e.model_dump()))
        for f in demo.estates.values():
            await s.merge(EstateSQL(**f.model_dump()))
        for homes in demo.homes.values():
            for v in homes:
                await s.merge(HomeSQL(**v.model_dump()))
        await s.flush()  # los equipos apuntan a fincas y especialistas
        for equipment_list in demo.equipment_list.values():
            for eq in equipment_list:
                await s.merge(EquipmentSQL(**eq.model_dump()))
        for admin_id, cfg in demo.administrator_config.items():
            await s.merge(
                ConfigSQL(
                    id=f"cfg-{admin_id}-v1",
                    scope="administrator",
                    scope_id=admin_id,
                    document=cfg.model_dump(exclude_none=True),
                )
            )
        for estate_id, (cfg_id, cfg) in demo.estate_config.items():
            await s.merge(
                ConfigSQL(
                    id=cfg_id,
                    scope="estate",
                    scope_id=estate_id,
                    document=cfg.model_dump(exclude_none=True),
                )
            )


if __name__ == "__main__":
    asyncio.run(seed(settings.database_url))
    print("Datos de demostración cargados.")
