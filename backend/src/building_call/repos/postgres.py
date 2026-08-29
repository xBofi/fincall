"""Repositorio PostgreSQL (SQLAlchemy async + asyncpg)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
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
from building_call.domain.operaciones import Usuario
from building_call.repos.modelos_sql import (
    AdministradorSQL,
    AuditoriaEventoSQL,
    ConfiguracionSQL,
    EspecialistaSQL,
    FincaSQL,
    IncidenciaSQL,
    LlamadaDecisionSQL,
    LlamadaSQL,
    LlamadaTurnoSQL,
    PistaAudioSQL,
    SesionSQL,
    UsuarioSQL,
    ViviendaSQL,
)
from building_call.repos.operativo import SesionAcceso


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

    # ------------------------------------------------------------- operativo (acceso y trazabilidad)

    async def guardar_usuario(self, usuario: Usuario) -> None:
        async with self.sesiones() as s, s.begin():
            await s.merge(UsuarioSQL(**usuario.model_dump()))

    async def obtener_usuario(self, usuario_id: str) -> Usuario | None:
        async with self.sesiones() as s:
            u = await s.get(UsuarioSQL, usuario_id)
            return Usuario.model_validate(u, from_attributes=True) if u else None

    async def obtener_usuario_por_nombre(self, nombre_usuario: str) -> Usuario | None:
        async with self.sesiones() as s:
            u = (
                await s.execute(
                    select(UsuarioSQL).where(UsuarioSQL.nombre_usuario == nombre_usuario)
                )
            ).scalar_one_or_none()
            return Usuario.model_validate(u, from_attributes=True) if u else None

    async def listar_usuarios(self) -> list[Usuario]:
        async with self.sesiones() as s:
            filas = (await s.execute(select(UsuarioSQL))).scalars()
            return [Usuario.model_validate(u, from_attributes=True) for u in filas]

    async def eliminar_usuario(self, usuario_id: str) -> None:
        async with self.sesiones() as s, s.begin():
            await s.execute(delete(SesionSQL).where(SesionSQL.usuario_id == usuario_id))
            await s.execute(delete(UsuarioSQL).where(UsuarioSQL.id == usuario_id))

    async def revocar_sesiones_de(self, usuario_id: str, cuando: datetime) -> None:
        async with self.sesiones() as s, s.begin():
            await s.execute(
                update(SesionSQL)
                .where(SesionSQL.usuario_id == usuario_id, SesionSQL.revocada_en.is_(None))
                .values(revocada_en=cuando)
            )

    async def guardar_sesion(self, sesion: SesionAcceso) -> None:
        async with self.sesiones() as s, s.begin():
            await s.merge(
                SesionSQL(
                    id=sesion.id,
                    usuario_id=sesion.usuario_id,
                    token_hash=sesion.token_hash,
                    creada_en=sesion.creada_en,
                    expira_en=sesion.expira_en,
                    revocada_en=sesion.revocada_en,
                )
            )

    async def obtener_sesion_por_hash(self, token_hash: str) -> SesionAcceso | None:
        async with self.sesiones() as s:
            fila = (
                await s.execute(select(SesionSQL).where(SesionSQL.token_hash == token_hash))
            ).scalar_one_or_none()
            if fila is None:
                return None
            return SesionAcceso(
                fila.id,
                fila.usuario_id,
                fila.token_hash,
                fila.creada_en,
                fila.expira_en,
                fila.revocada_en,
            )

    async def revocar_sesion(self, token_hash: str, cuando: datetime) -> None:
        async with self.sesiones() as s, s.begin():
            await s.execute(
                update(SesionSQL)
                .where(SesionSQL.token_hash == token_hash)
                .values(revocada_en=cuando)
            )

    async def listar_llamadas(self, estado: str | None = None, limite: int = 100) -> list[Llamada]:
        async with self.sesiones() as s:
            q = select(LlamadaSQL).order_by(LlamadaSQL.iniciada_en.desc()).limit(limite)
            if estado:
                q = q.where(LlamadaSQL.estado == estado)
            return [
                Llamada.model_validate(f, from_attributes=True)
                for f in (await s.execute(q)).scalars()
            ]

    async def obtener_llamada(self, llamada_id: str) -> Llamada | None:
        async with self.sesiones() as s:
            f = await s.get(LlamadaSQL, llamada_id)
            return Llamada.model_validate(f, from_attributes=True) if f else None

    async def guardar_turno(self, llamada_id: str, turno: int, datos: dict[str, Any]) -> None:
        async with self.sesiones() as s, s.begin():
            await s.merge(
                LlamadaTurnoSQL(
                    id=f"{llamada_id}:{turno}", llamada_id=llamada_id, turno=turno, datos=datos
                )
            )

    async def listar_turnos(self, llamada_id: str) -> list[dict[str, Any]]:
        async with self.sesiones() as s:
            q = (
                select(LlamadaTurnoSQL)
                .where(LlamadaTurnoSQL.llamada_id == llamada_id)
                .order_by(LlamadaTurnoSQL.turno)
            )
            return [{"turno": f.turno, **f.datos} for f in (await s.execute(q)).scalars()]

    async def guardar_decision(
        self, llamada_id: str, turno: int, nodo: str, datos: dict[str, Any]
    ) -> None:
        async with self.sesiones() as s, s.begin():
            s.add(
                LlamadaDecisionSQL(
                    id=f"dec-{uuid4().hex[:16]}",
                    llamada_id=llamada_id,
                    turno=turno,
                    nodo=nodo,
                    datos=datos,
                )
            )

    async def listar_decisiones(self, llamada_id: str) -> list[dict[str, Any]]:
        async with self.sesiones() as s:
            q = (
                select(LlamadaDecisionSQL)
                .where(LlamadaDecisionSQL.llamada_id == llamada_id)
                .order_by(LlamadaDecisionSQL.turno, LlamadaDecisionSQL.creada_en)
            )
            return [
                {"turno": f.turno, "nodo": f.nodo, **f.datos}
                for f in (await s.execute(q)).scalars()
            ]

    async def guardar_pista(self, pista: dict[str, Any]) -> None:
        async with self.sesiones() as s, s.begin():
            await s.merge(PistaAudioSQL(**pista))

    def _pista_dict(self, f: PistaAudioSQL) -> dict[str, Any]:
        return {
            c: getattr(f, c)
            for c in (
                "id", "llamada_id", "canal", "clave_objeto", "mime", "checksum", "duracion_ms",
                "creada_en", "expira_en", "eliminada_en",
            )
        }  # fmt: skip

    async def listar_pistas(self, llamada_id: str) -> list[dict[str, Any]]:
        async with self.sesiones() as s:
            q = select(PistaAudioSQL).where(PistaAudioSQL.llamada_id == llamada_id)
            return [self._pista_dict(f) for f in (await s.execute(q)).scalars()]

    async def obtener_pista(self, pista_id: str) -> dict[str, Any] | None:
        async with self.sesiones() as s:
            f = await s.get(PistaAudioSQL, pista_id)
            return self._pista_dict(f) if f else None

    async def pistas_vencidas(self, ahora: datetime) -> list[dict[str, Any]]:
        async with self.sesiones() as s:
            q = select(PistaAudioSQL).where(
                PistaAudioSQL.expira_en <= ahora, PistaAudioSQL.eliminada_en.is_(None)
            )
            return [self._pista_dict(f) for f in (await s.execute(q)).scalars()]

    async def marcar_pista_eliminada(self, pista_id: str, cuando: datetime) -> None:
        async with self.sesiones() as s, s.begin():
            await s.execute(
                update(PistaAudioSQL)
                .where(PistaAudioSQL.id == pista_id)
                .values(eliminada_en=cuando)
            )

    async def registrar_auditoria(
        self, usuario_id: str | None, accion: str, recurso: str, detalle: dict[str, Any]
    ) -> None:
        async with self.sesiones() as s, s.begin():
            s.add(
                AuditoriaEventoSQL(
                    id=f"aud-{uuid4().hex[:16]}",
                    usuario_id=usuario_id,
                    accion=accion,
                    recurso=recurso,
                    detalle=detalle,
                )
            )

    async def listar_auditoria(self, limite: int = 200) -> list[dict[str, Any]]:
        async with self.sesiones() as s:
            q = (
                select(AuditoriaEventoSQL)
                .order_by(AuditoriaEventoSQL.ocurrido_en.desc())
                .limit(limite)
            )
            return [
                {
                    "usuario_id": f.usuario_id,
                    "accion": f.accion,
                    "recurso": f.recurso,
                    "detalle": f.detalle,
                    "ocurrido_en": f.ocurrido_en.isoformat() if f.ocurrido_en else None,
                }
                for f in (await s.execute(q)).scalars()
            ]

    async def listar_incidencias(self) -> list[Incidencia]:
        async with self.sesiones() as s:
            filas = (await s.execute(select(IncidenciaSQL))).scalars()
            return [Incidencia.model_validate(i, from_attributes=True) for i in filas]
