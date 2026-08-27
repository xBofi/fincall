"""Repositorio PostgreSQL (SQLAlchemy async + asyncpg)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from building_call.domain.configuracion import (
    ConfiguracionEfectiva,
    ConfiguracionParcial,
    resolver,
)
from building_call.domain.entidades import (
    Administrador,
    Especialista,
    Finca,
    Incidencia,
    Llamada,
    Vivienda,
)
from building_call.repos.modelos_sql import (
    AdministradorSQL,
    ConfiguracionSQL,
    EspecialistaSQL,
    FincaSQL,
    IncidenciaSQL,
    LlamadaSQL,
    ViviendaSQL,
)


class RepositorioPostgres:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.sesiones = async_sessionmaker(self.engine, expire_on_commit=False)

    async def _ultima_configuracion(
        self, s: AsyncSession, ambito: str, ambito_id: str
    ) -> ConfiguracionSQL | None:
        q = (
            select(ConfiguracionSQL)
            .where(ConfiguracionSQL.ambito == ambito, ConfiguracionSQL.ambito_id == ambito_id)
            .order_by(ConfiguracionSQL.valido_desde.desc())
            .limit(1)
        )
        return (await s.execute(q)).scalar_one_or_none()

    async def listar_fincas(self) -> list[Finca]:
        async with self.sesiones() as s:
            filas = (await s.execute(select(FincaSQL))).scalars()
            return [Finca.model_validate(f, from_attributes=True) for f in filas]

    async def obtener_finca(self, finca_id: str) -> Finca | None:
        async with self.sesiones() as s:
            f = await s.get(FincaSQL, finca_id)
            return Finca.model_validate(f, from_attributes=True) if f else None

    async def obtener_administrador(self, administrador_id: str) -> Administrador | None:
        async with self.sesiones() as s:
            a = await s.get(AdministradorSQL, administrador_id)
            return Administrador.model_validate(a, from_attributes=True) if a else None

    async def viviendas_de(self, finca_id: str) -> list[Vivienda]:
        async with self.sesiones() as s:
            q = select(ViviendaSQL).where(ViviendaSQL.finca_id == finca_id)
            filas = (await s.execute(q)).scalars()
            return [Vivienda.model_validate(v, from_attributes=True) for v in filas]

    async def especialistas_de(self, administrador_id: str) -> list[Especialista]:
        async with self.sesiones() as s:
            q = select(EspecialistaSQL).where(EspecialistaSQL.administrador_id == administrador_id)
            filas = (await s.execute(q)).scalars()
            return [Especialista.model_validate(e, from_attributes=True) for e in filas]

    async def configuracion_efectiva(
        self, finca_id: str
    ) -> tuple[ConfiguracionEfectiva, str | None]:
        async with self.sesiones() as s:
            finca = await s.get(FincaSQL, finca_id)
            if finca is None:
                raise KeyError(finca_id)
            cfg_adm = await self._ultima_configuracion(s, "administrador", finca.administrador_id)
            cfg_fin = await self._ultima_configuracion(s, "finca", finca_id)
            efectiva = resolver(
                ConfiguracionParcial.model_validate(cfg_adm.documento) if cfg_adm else None,
                ConfiguracionParcial.model_validate(cfg_fin.documento) if cfg_fin else None,
            )
            return efectiva, (cfg_fin.id if cfg_fin else None)

    async def guardar_llamada(self, llamada: Llamada) -> None:
        async with self.sesiones() as s, s.begin():
            await s.merge(LlamadaSQL(**llamada.model_dump()))

    async def guardar_incidencia(self, incidencia: Incidencia) -> None:
        async with self.sesiones() as s, s.begin():
            await s.merge(IncidenciaSQL(**incidencia.model_dump()))

    async def listar_incidencias(self) -> list[Incidencia]:
        async with self.sesiones() as s:
            filas = (await s.execute(select(IncidenciaSQL))).scalars()
            return [Incidencia.model_validate(i, from_attributes=True) for i in filas]
