"""Carga los datos de demostración del repositorio en memoria en PostgreSQL.

Uso: `uv run python -m building_call.repos.semilla` (requiere BC_DATABASE_URL y migraciones aplicadas).
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from building_call.repos.memoria import RepositorioMemoria
from building_call.repos.modelos_sql import (
    AdministradorSQL,
    ConfiguracionSQL,
    EspecialistaSQL,
    FincaSQL,
    ViviendaSQL,
)
from building_call.settings import settings


async def sembrar(database_url: str) -> None:
    demo = RepositorioMemoria()
    engine = create_async_engine(database_url)
    sesiones = async_sessionmaker(engine, expire_on_commit=False)
    async with sesiones() as s, s.begin():
        for a in demo.administradores.values():
            await s.merge(AdministradorSQL(**a.model_dump()))
        for e in demo.especialistas.values():
            await s.merge(EspecialistaSQL(**e.model_dump()))
        for f in demo.fincas.values():
            await s.merge(FincaSQL(**f.model_dump()))
        for viviendas in demo.viviendas.values():
            for v in viviendas:
                await s.merge(ViviendaSQL(**v.model_dump()))
        for adm_id, cfg in demo.config_administrador.items():
            await s.merge(
                ConfiguracionSQL(
                    id=f"cfg-{adm_id}-v1",
                    ambito="administrador",
                    ambito_id=adm_id,
                    documento=cfg.model_dump(exclude_none=True),
                )
            )
        for finca_id, (cfg_id, cfg) in demo.config_finca.items():
            await s.merge(
                ConfiguracionSQL(
                    id=cfg_id,
                    ambito="finca",
                    ambito_id=finca_id,
                    documento=cfg.model_dump(exclude_none=True),
                )
            )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(sembrar(settings.database_url))
    print("Datos de demostración cargados.")
