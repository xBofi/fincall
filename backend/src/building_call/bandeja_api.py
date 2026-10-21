"""Bandeja del administrador: incidencias que esperan su decisión y lo que hace con ellas.

Con las reglas de enrutamiento (2026-10-21) muchas incidencias llegan sin especialista y en
estado `pendiente`, con una sugerencia y un motivo. Aquí se listan, se ven con su llamada y se
asignan, cambian de estado o se anotan. Calidad solo lee. Cada acción queda en el historial de la
incidencia y en la auditoría.

Asignar NO avisa a nadie: deja constancia de que el aviso queda pendiente de envío para cuando
existan avisos reales.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from building_call.acceso import Acceso
from building_call.domain.configuracion import URGENCIAS, Urgencia
from building_call.domain.entidades import (
    AccionIncidencia,
    EntradaHistorialIncidencia,
    EstadoIncidencia,
    Incidencia,
)
from building_call.domain.operaciones import Rol, Usuario
from building_call.repos.base import Repositorio
from building_call.repos.operativo import RepositorioOperativo

LECTORES = (Rol.owner, Rol.administrador, Rol.calidad)
GESTORES = (Rol.owner, Rol.administrador)

#: Estados a los que se llega a mano. `asignada` solo se alcanza asignando a un especialista.
EstadoManual = Literal["en_curso", "esperando_informacion", "resuelta", "cerrada"]
#: Estado final: una incidencia cerrada ya no se toca
FINALES = {"cerrada"}
#: Lo resuelto ya no se reasigna: si vuelve a fallar, primero se reabre (en curso)
NO_ASIGNABLES = {"resuelta", "cerrada"}
AVISO_PENDIENTE = "pendiente_envio"

_PESO_URGENCIA = {u: i for i, u in enumerate(reversed(URGENCIAS))}  # emergencia = 0


class PeticionAsignar(BaseModel):
    especialista_id: str = Field(min_length=1, max_length=64)
    nota: str | None = Field(default=None, max_length=2000)


class PeticionEstado(BaseModel):
    estado: EstadoManual
    nota: str | None = Field(default=None, max_length=2000)


class PeticionNota(BaseModel):
    texto: str = Field(max_length=2000)

    @field_validator("texto")
    @classmethod
    def no_vacia(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("La nota no puede estar vacía")
        return valor


def pendiente_administrador(inc: Incidencia) -> bool:
    return inc.especialista_id is None and inc.estado == "pendiente"


def _limpia(nota: str | None) -> str | None:
    return (nota or "").strip() or None


def crear_router_bandeja(
    repo: Repositorio, operativo: RepositorioOperativo, acceso: Acceso
) -> APIRouter:
    r = APIRouter(prefix="/api/bandeja")
    lector = acceso.requiere(*LECTORES)
    gestor = acceso.requiere(*GESTORES)

    async def _incidencia(incidencia_id: str) -> Incidencia:
        inc = await repo.obtener_incidencia(incidencia_id)
        if inc is None:
            raise HTTPException(404, "Incidencia no encontrada")
        return inc

    async def _especialistas(finca_id: str) -> dict[str, Any]:
        finca = await repo.obtener_finca(finca_id)
        if finca is None:
            return {}
        return {e.id: e for e in await repo.especialistas_de(finca.administrador_id)}

    async def _registrar(
        usuario: Usuario,
        antes: Incidencia,
        despues: Incidencia,
        accion: AccionIncidencia,
        nota: str | None,
        **detalle: Any,
    ) -> EntradaHistorialIncidencia:
        entrada = EntradaHistorialIncidencia(
            id=f"hist-{uuid4().hex[:16]}",
            incidencia_id=antes.id,
            accion=accion,
            estado_anterior=antes.estado,
            estado_nuevo=despues.estado,
            usuario_id=usuario.id,
            nota=nota,
            detalle=detalle,
            cambiado_en=datetime.now(UTC),
        )
        if despues is not antes:
            await repo.guardar_incidencia(despues)
        await repo.registrar_historial_incidencia(entrada)
        await operativo.registrar_auditoria(
            usuario.id,
            {
                "asignar": "asignar_incidencia",
                "cambiar_estado": "cambiar_estado_incidencia",
                "nota": "nota_incidencia",
            }[accion],
            f"incidencia:{antes.id}",
            {
                "estado_anterior": antes.estado,
                "estado_nuevo": despues.estado,
                **({"nota": nota} if nota else {}),
                **detalle,
            },
        )
        return entrada

    async def _respuesta(inc: Incidencia, entrada: EntradaHistorialIncidencia) -> dict[str, Any]:
        return {
            "incidencia": inc.model_dump(mode="json"),
            "historial": entrada.model_dump(mode="json"),
            **({"aviso": entrada.detalle["aviso"]} if "aviso" in entrada.detalle else {}),
        }

    @r.get("/incidencias")
    async def listar(
        estado: EstadoIncidencia | None = None,
        pendientes_administrador: bool = False,
        finca_id: str | None = None,
        urgencia: Urgencia | None = None,
        limite: int = Query(default=200, ge=1, le=1000),
        _: Usuario = Depends(lector),  # noqa: B008
    ) -> list[dict[str, Any]]:
        filas = [
            i
            for i in await repo.listar_incidencias()
            if (estado is None or i.estado == estado)
            and (not pendientes_administrador or pendiente_administrador(i))
            and (finca_id is None or i.finca_id == finca_id)
            and (urgencia is None or i.urgencia == urgencia)
        ]
        # Lo que espera al administrador primero; dentro, lo más urgente y lo más reciente
        filas.sort(key=lambda i: i.creada_en, reverse=True)
        filas.sort(key=lambda i: (not pendiente_administrador(i), _PESO_URGENCIA[i.urgencia]))
        filas = filas[:limite]

        fincas = {f.id: f for f in await repo.listar_fincas()}
        viviendas: dict[str, str] = {}
        especialistas: dict[str, str] = {}
        for fid in {i.finca_id for i in filas}:
            viviendas.update({v.id: v.etiqueta() for v in await repo.viviendas_de(fid)})
            if fid in fincas:
                especialistas.update(
                    {
                        e.id: e.nombre
                        for e in await repo.especialistas_de(fincas[fid].administrador_id)
                    }
                )
        salida = []
        for i in filas:
            llamada = await operativo.obtener_llamada(i.llamada_id)
            finca = fincas.get(i.finca_id)
            salida.append(
                {
                    **i.model_dump(mode="json"),
                    "pendiente_administrador": pendiente_administrador(i),
                    "finca_direccion": finca.direccion if finca else None,
                    "vivienda": viviendas.get(i.vivienda_id),
                    "especialista_nombre": especialistas.get(i.especialista_id or ""),
                    "motivo_revision": llamada.motivo_revision if llamada else None,
                }
            )
        return salida

    @r.get("/incidencias/{incidencia_id}")
    async def detalle(
        incidencia_id: str,
        usuario: Usuario = Depends(lector),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incidencia(incidencia_id)
        await operativo.registrar_auditoria(
            usuario.id, "ver_incidencia", f"incidencia:{incidencia_id}", {}
        )
        finca = await repo.obtener_finca(inc.finca_id)
        especialistas = await _especialistas(inc.finca_id)
        vivienda = next(
            (v for v in await repo.viviendas_de(inc.finca_id) if v.id == inc.vivienda_id), None
        )
        equipo = next(
            (e for e in await repo.equipos_de(inc.finca_id) if e.id == inc.equipo_id), None
        )
        llamada = await operativo.obtener_llamada(inc.llamada_id)
        asignado = especialistas.get(inc.especialista_id or "")
        return {
            "incidencia": inc.model_dump(mode="json"),
            "pendiente_administrador": pendiente_administrador(inc),
            "finca": finca.model_dump(mode="json") if finca else None,
            "vivienda": vivienda.etiqueta() if vivienda else None,
            "llamada": llamada.model_dump(
                mode="json",
                include={
                    "id",
                    "iniciada_en",
                    "telefono_llamante",
                    "nombre_declarado",
                    "resumen",
                    "transcripcion",
                    "resultado",
                    "motivo_revision",
                },
            )
            if llamada
            else None,
            "equipo": {
                **equipo.model_dump(mode="json"),
                "descripcion": equipo.descripcion(),
                "mantenedor": (
                    especialistas[equipo.mantenedor_id].model_dump(mode="json")
                    if equipo.mantenedor_id in especialistas
                    else None
                ),
            }
            if equipo
            else None,
            "especialista": asignado.model_dump(mode="json") if asignado else None,
            "especialistas": [e.model_dump(mode="json") for e in especialistas.values()],
            "historial": [
                {**h.model_dump(mode="json"), "usuario_nombre": await _nombre(h.usuario_id)}
                for h in await repo.listar_historial_incidencia(inc.id)
            ],
        }

    async def _nombre(usuario_id: str | None) -> str | None:
        if usuario_id is None:
            return None
        u = await operativo.obtener_usuario(usuario_id)
        return (u.nombre or u.nombre_usuario) if u else None

    @r.post("/incidencias/{incidencia_id}/asignar")
    async def asignar(
        incidencia_id: str,
        peticion: PeticionAsignar,
        usuario: Usuario = Depends(gestor),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incidencia(incidencia_id)
        if inc.estado in NO_ASIGNABLES:
            raise HTTPException(409, f"Una incidencia {inc.estado} no se asigna")
        especialista = (await _especialistas(inc.finca_id)).get(peticion.especialista_id)
        if especialista is None:
            raise HTTPException(422, "El especialista no pertenece al administrador de la finca")
        nueva = inc.model_copy(update={"especialista_id": especialista.id, "estado": "asignada"})
        # Sin avisos reales todavía: el aviso queda anotado como pendiente de envío
        entrada = await _registrar(
            usuario,
            inc,
            nueva,
            "asignar",
            _limpia(peticion.nota),
            especialista_id=especialista.id,
            especialista_nombre=especialista.nombre,
            especialista_anterior=inc.especialista_id,
            canal=especialista.canal_contacto,
            aviso=AVISO_PENDIENTE,
        )
        return await _respuesta(nueva, entrada)

    @r.post("/incidencias/{incidencia_id}/estado")
    async def cambiar_estado(
        incidencia_id: str,
        peticion: PeticionEstado,
        usuario: Usuario = Depends(gestor),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incidencia(incidencia_id)
        if inc.estado in FINALES:
            raise HTTPException(409, "La incidencia está cerrada")
        if peticion.estado == inc.estado:
            raise HTTPException(409, f"La incidencia ya está {inc.estado}")
        nueva = inc.model_copy(update={"estado": peticion.estado})
        entrada = await _registrar(usuario, inc, nueva, "cambiar_estado", _limpia(peticion.nota))
        return await _respuesta(nueva, entrada)

    @r.post("/incidencias/{incidencia_id}/nota")
    async def anotar(
        incidencia_id: str,
        peticion: PeticionNota,
        usuario: Usuario = Depends(gestor),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incidencia(incidencia_id)
        entrada = await _registrar(usuario, inc, inc, "nota", peticion.texto)
        return await _respuesta(inc, entrada)

    return r
