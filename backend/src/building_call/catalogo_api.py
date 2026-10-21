"""API del catálogo: fincas, viviendas, profesionales, equipos y quién atiende cada categoría.

Es exactamente lo que el asistente consulta en cada llamada: con qué direcciones reconoce una
finca, qué viviendas acepta, a qué profesional avisa y qué equipo tiene mantenedor propio.

- **Lectura**: cualquier usuario de la consola.
- **Escritura**: `owner` y `administrador`. Cada cambio queda auditado con el antes y el después.
- **Ids**: los genera el servidor; los que mande el cliente se ignoran.
- **Configuración**: quién atiende cada categoría y el contacto de la finca viven en capas
  versionadas. Editar crea una versión nueva; la anterior se conserva.
- **Borrar**: no se borra nada que tenga historial (llamadas o incidencias), y un profesional
  que el asistente usa solo se borra si se pide expresamente (`forzar`), diciendo qué se
  desasigna.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Literal, Self
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from building_call.acceso import Acceso
from building_call.domain.configuracion import (
    ConfiguracionParcial,
    Contacto,
    resolver,
)
from building_call.domain.entidades import (
    Administrador,
    Equipo,
    Especialista,
    Finca,
    Vivienda,
)
from building_call.domain.operaciones import Rol, Usuario
from building_call.repos.base import Repositorio
from building_call.repos.catalogo import RepositorioCatalogo
from building_call.repos.operativo import RepositorioOperativo

LECTORES = (Rol.owner, Rol.administrador, Rol.calidad)
EDITORES = (Rol.owner, Rol.administrador)

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TELEFONO = re.compile(r"^\+?\d{6,15}$")


def _normalizar(texto: str) -> str:
    """'Calle  Mayor 12' y 'calle mayor 12' son la misma finca para el asistente."""
    sin_tildes = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def _nuevo_id(prefijo: str, texto: str | None = None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _normalizar(texto or "")).strip("-")[:32].strip("-")
    sufijo = uuid4().hex[:6]
    return f"{prefijo}-{slug}-{sufijo}" if slug else f"{prefijo}-{uuid4().hex[:12]}"


def _telefono(valor: str) -> str:
    limpio = re.sub(r"[\s.-]", "", valor)
    if not _TELEFONO.fullmatch(limpio):
        raise ValueError("teléfono no válido: solo dígitos, con prefijo + opcional")
    return limpio


def _opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    valor = valor.strip()
    return valor or None


# ----------------------------------------------------------------------------- cuerpos de entrada


class FincaEntrada(BaseModel):
    direccion: str = Field(min_length=3, max_length=300)
    correo: str = Field(max_length=200)
    telefono_oficial: str = Field(max_length=32)

    @field_validator("direccion")
    @classmethod
    def _direccion(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    @field_validator("correo")
    @classmethod
    def _correo(cls, v: str) -> str:
        v = v.strip()
        if not _CORREO.fullmatch(v):
            raise ValueError("correo no válido")
        return v

    @field_validator("telefono_oficial")
    @classmethod
    def _tel(cls, v: str) -> str:
        return _telefono(v)


class FincaNueva(FincaEntrada):
    administrador_id: str = Field(min_length=1, max_length=64)


class ViviendaEntrada(BaseModel):
    portal: str | None = Field(default=None, max_length=16)
    piso: str = Field(min_length=1, max_length=16)
    puerta: str = Field(min_length=1, max_length=16)

    @field_validator("portal")
    @classmethod
    def _portal(cls, v: str | None) -> str | None:
        return _opcional(v)

    @field_validator("piso")
    @classmethod
    def _piso(cls, v: str) -> str:
        v = v.strip().rstrip("ºª°").strip()
        if not v:
            raise ValueError("falta el piso")
        return v

    @field_validator("puerta")
    @classmethod
    def _puerta(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("falta la puerta")
        return v


class EquipoEntrada(BaseModel):
    tipo: str = Field(min_length=2, max_length=120)
    ubicacion: str | None = Field(default=None, max_length=200)
    marca: str | None = Field(default=None, max_length=120)
    modelo: str | None = Field(default=None, max_length=120)
    instalador: str | None = Field(default=None, max_length=200)
    mantenedor_id: str | None = Field(default=None, max_length=64)
    contrato: str | None = Field(default=None, max_length=120)
    garantia_hasta: date | None = None

    @field_validator("tipo")
    @classmethod
    def _tipo(cls, v: str) -> str:
        return v.strip()

    @field_validator("ubicacion", "marca", "modelo", "instalador", "mantenedor_id", "contrato")
    @classmethod
    def _limpios(cls, v: str | None) -> str | None:
        return _opcional(v)


class EspecialistaEntrada(BaseModel):
    nombre: str = Field(min_length=2, max_length=200)
    categoria_principal: str = Field(min_length=1, max_length=64)
    canal_contacto: Literal["correo", "llamada"]
    destino: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _destino_segun_canal(self) -> Self:
        self.nombre = self.nombre.strip()
        destino = self.destino.strip()
        if self.canal_contacto == "correo":
            if not _CORREO.fullmatch(destino):
                raise ValueError("con canal correo, el destino debe ser un correo")
            self.destino = destino
        else:
            try:
                self.destino = _telefono(destino)
            except ValueError as e:
                raise ValueError("con canal llamada, el destino debe ser un teléfono") from e
        return self


class EspecialistaNuevo(EspecialistaEntrada):
    administrador_id: str = Field(min_length=1, max_length=64)


class Asignacion(BaseModel):
    especialista_id: str | None = None


# ------------------------------------------------------------------------------------- router


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def crear_router_catalogo(
    negocio: Repositorio,
    catalogo: RepositorioCatalogo,
    operativo: RepositorioOperativo,
    acceso: Acceso,
) -> APIRouter:
    r = APIRouter(prefix="/api/catalogos")
    lector = acceso.requiere(*LECTORES)
    editor = acceso.requiere(*EDITORES)

    async def auditar(usuario: Usuario, accion: str, recurso: str, **detalle: Any) -> None:
        await operativo.registrar_auditoria(usuario.id, accion, recurso, detalle)

    # --------------------------------------------------------------------------- ayudas comunes

    async def _finca_o_404(finca_id: str) -> Finca:
        f = await negocio.obtener_finca(finca_id)
        if f is None:
            raise HTTPException(404, "Finca desconocida")
        return f

    async def _admin_o_404(admin_id: str) -> Administrador:
        a = await negocio.obtener_administrador(admin_id)
        if a is None:
            raise HTTPException(404, "Administrador desconocido")
        return a

    async def _especialista_o_404(especialista_id: str) -> Especialista:
        e = await catalogo.obtener_especialista(especialista_id)
        if e is None:
            raise HTTPException(404, "Profesional desconocido")
        return e

    async def _catalogo_de(admin_id: str) -> list[str]:
        capa, _ = await catalogo.configuracion_de("administrador", admin_id)
        return resolver(capa).catalogo_categorias

    def _vista_especialista(e: Especialista, admin: Administrador | None) -> dict[str, Any]:
        return {
            **e.model_dump(),
            "administrador": admin.nombre if admin else e.administrador_id,
        }

    def _vista_equipo(e: Equipo) -> dict[str, Any]:
        return {**e.model_dump(mode="json"), "descripcion": e.descripcion()}

    async def _detalle_finca(f: Finca) -> dict[str, Any]:
        admin = await negocio.obtener_administrador(f.administrador_id)
        viviendas = await negocio.viviendas_de(f.id)
        cfg, cfg_id = await negocio.configuracion_efectiva(f.id)
        return {
            "id": f.id,
            "direccion": f.direccion,
            "administrador_id": f.administrador_id,
            "administrador": admin.nombre if admin else f.administrador_id,
            "correo": f.correo,
            "telefono_oficial": f.telefono_oficial,
            "viviendas": [
                {"id": v.id, "portal": v.portal, "piso": v.piso, "puerta": v.puerta}
                for v in viviendas
            ],
            # Equipos con quien los mantiene: se avisa a su mantenedor antes que al especialista
            "equipos": [_vista_equipo(e) for e in await negocio.equipos_de(f.id)],
            # Las tres capas ya resueltas: la forma más rápida de entender por qué una finca se
            # comporta distinto de otra
            "configuracion_efectiva": cfg.model_dump(),
            "configuracion_id": cfg_id,
        }

    async def _vista_administrador(admin: Administrador) -> dict[str, Any]:
        fincas = [f for f in await negocio.listar_fincas() if f.administrador_id == admin.id]
        capa, cfg_id = await catalogo.configuracion_de("administrador", admin.id)
        resuelta = resolver(capa)
        return {
            "id": admin.id,
            "nombre": admin.nombre,
            "fincas": [{"id": f.id, "direccion": f.direccion} for f in fincas],
            "especialistas": [
                {
                    "id": e.id,
                    "nombre": e.nombre,
                    "categoria_principal": e.categoria_principal,
                    "canal_contacto": e.canal_contacto,
                    "destino": e.destino,
                }
                for e in await negocio.especialistas_de(admin.id)
            ],
            # Capa del administrador: la heredan sus fincas salvo que una la sobrescriba
            "catalogo_categorias": resuelta.catalogo_categorias,
            "especialista_por_categoria": resuelta.especialista_por_categoria,
            "configuracion_id": cfg_id,
        }

    async def _direccion_libre(direccion: str, salvo: str | None = None) -> None:
        clave = _normalizar(direccion)
        for f in await negocio.listar_fincas():
            if f.id != salvo and _normalizar(f.direccion) == clave:
                raise HTTPException(
                    409,
                    f"Ya existe una finca en «{f.direccion}»: el asistente no sabría cuál de "
                    "las dos es la del vecino.",
                )

    async def _vivienda_libre(finca_id: str, v: ViviendaEntrada, salvo: str | None = None) -> None:
        clave = (_normalizar(v.portal or ""), _normalizar(v.piso), _normalizar(v.puerta))
        for x in await negocio.viviendas_de(finca_id):
            if x.id == salvo:
                continue
            if (_normalizar(x.portal or ""), _normalizar(x.piso), _normalizar(x.puerta)) == clave:
                raise HTTPException(409, f"La vivienda {x.etiqueta()} ya existe en esta finca.")

    async def _vivienda_o_404(finca_id: str, vivienda_id: str) -> Vivienda:
        v = next((x for x in await negocio.viviendas_de(finca_id) if x.id == vivienda_id), None)
        if v is None:
            raise HTTPException(404, "Vivienda desconocida en esta finca")
        return v

    async def _equipo_o_404(finca_id: str, equipo_id: str) -> Equipo:
        e = next((x for x in await negocio.equipos_de(finca_id) if x.id == equipo_id), None)
        if e is None:
            raise HTTPException(404, "Equipo desconocido en esta finca")
        return e

    async def _mantenedor_valido(finca: Finca, mantenedor_id: str | None) -> None:
        if mantenedor_id is None:
            return
        e = await catalogo.obtener_especialista(mantenedor_id)
        if e is None or e.administrador_id != finca.administrador_id:
            raise HTTPException(
                422,
                "El mantenedor debe ser un profesional del mismo administrador que la finca: "
                "el asistente le avisará por su canal.",
            )

    async def _sin_historial(campo: Any, valor: str, que: str) -> None:
        refs = await catalogo.contar_referencias(campo, valor)
        if refs["llamadas"] or refs["incidencias"]:
            raise HTTPException(
                409,
                f"No se puede borrar {que}: tiene "
                f"{_plural(refs['llamadas'], 'llamada', 'llamadas')} y "
                f"{_plural(refs['incidencias'], 'incidencia', 'incidencias')} registradas, y "
                "borrarla dejaría ese historial sin su referencia.",
            )

    async def _sincronizar_contacto(f: Finca) -> str | None:
        """El asistente avisa al contacto de la capa de finca: se mantiene igual que la ficha."""
        capa, version = await catalogo.configuracion_de("finca", f.id)
        contacto = Contacto(correo=f.correo, telefono_oficial=f.telefono_oficial)
        if capa is not None and capa.contacto == contacto:
            return version
        nueva = (capa or ConfiguracionParcial()).model_copy(update={"contacto": contacto})
        return await catalogo.guardar_configuracion("finca", f.id, nueva)

    # -------------------------------------------------------------------------------- lectura

    @r.get("/administradores")
    async def administradores(_: Usuario = Depends(lector)) -> list[dict[str, Any]]:
        return [await _vista_administrador(a) for a in await catalogo.listar_administradores()]

    @r.get("/fincas")
    async def fincas(_: Usuario = Depends(lector)) -> list[dict[str, Any]]:
        salida = []
        for f in await negocio.listar_fincas():
            admin = await negocio.obtener_administrador(f.administrador_id)
            viviendas = await negocio.viviendas_de(f.id)
            salida.append(
                {
                    "id": f.id,
                    "direccion": f.direccion,
                    "administrador_id": f.administrador_id,
                    "administrador": admin.nombre if admin else f.administrador_id,
                    "correo": f.correo,
                    "telefono_oficial": f.telefono_oficial,
                    "viviendas": len(viviendas),
                }
            )
        return salida

    @r.get("/fincas/{finca_id}")
    async def finca(finca_id: str, _: Usuario = Depends(lector)) -> dict[str, Any]:
        return await _detalle_finca(await _finca_o_404(finca_id))

    @r.get("/especialistas")
    async def especialistas(_: Usuario = Depends(lector)) -> list[dict[str, Any]]:
        salida = []
        for admin in await catalogo.listar_administradores():
            for e in await negocio.especialistas_de(admin.id):
                salida.append(_vista_especialista(e, admin))
        return salida

    # --------------------------------------------------------------------------------- fincas

    @r.post("/fincas", status_code=201)
    async def crear_finca(cuerpo: FincaNueva, actor: Usuario = Depends(editor)) -> dict[str, Any]:
        if await negocio.obtener_administrador(cuerpo.administrador_id) is None:
            raise HTTPException(422, "Administrador desconocido")
        await _direccion_libre(cuerpo.direccion)
        f = Finca(id=_nuevo_id("finca", cuerpo.direccion), **cuerpo.model_dump())
        await catalogo.guardar_finca(f)
        version = await _sincronizar_contacto(f)
        await auditar(actor, "finca_creada", f.id, despues=f.model_dump(), configuracion_id=version)
        return await _detalle_finca(f)

    @r.put("/fincas/{finca_id}")
    async def editar_finca(
        finca_id: str, cuerpo: FincaEntrada, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        antes = await _finca_o_404(finca_id)
        await _direccion_libre(cuerpo.direccion, salvo=finca_id)
        f = antes.model_copy(update=cuerpo.model_dump())
        await catalogo.guardar_finca(f)
        version = await _sincronizar_contacto(f)
        await auditar(
            actor,
            "finca_editada",
            f.id,
            antes=antes.model_dump(),
            despues=f.model_dump(),
            configuracion_id=version,
        )
        return await _detalle_finca(f)

    @r.delete("/fincas/{finca_id}")
    async def borrar_finca(finca_id: str, actor: Usuario = Depends(editor)) -> dict[str, Any]:
        f = await _finca_o_404(finca_id)
        await _sin_historial("finca_id", finca_id, f"la finca «{f.direccion}»")
        viviendas = len(await negocio.viviendas_de(finca_id))
        equipos = len(await negocio.equipos_de(finca_id))
        await catalogo.eliminar_finca(finca_id)
        await auditar(
            actor,
            "finca_borrada",
            finca_id,
            antes=f.model_dump(),
            viviendas=viviendas,
            equipos=equipos,
        )
        return {"borrada": finca_id}

    # ------------------------------------------------------------------------------- viviendas

    @r.post("/fincas/{finca_id}/viviendas", status_code=201)
    async def crear_vivienda(
        finca_id: str, cuerpo: ViviendaEntrada, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        await _finca_o_404(finca_id)
        await _vivienda_libre(finca_id, cuerpo)
        v = Vivienda(id=_nuevo_id("viv"), finca_id=finca_id, **cuerpo.model_dump())
        await catalogo.guardar_vivienda(v)
        await auditar(actor, "vivienda_creada", v.id, finca_id=finca_id, despues=v.model_dump())
        return v.model_dump()

    @r.put("/fincas/{finca_id}/viviendas/{vivienda_id}")
    async def editar_vivienda(
        finca_id: str,
        vivienda_id: str,
        cuerpo: ViviendaEntrada,
        actor: Usuario = Depends(editor),
    ) -> dict[str, Any]:
        await _finca_o_404(finca_id)
        antes = await _vivienda_o_404(finca_id, vivienda_id)
        await _vivienda_libre(finca_id, cuerpo, salvo=vivienda_id)
        v = antes.model_copy(update=cuerpo.model_dump())
        await catalogo.guardar_vivienda(v)
        await auditar(
            actor,
            "vivienda_editada",
            v.id,
            finca_id=finca_id,
            antes=antes.model_dump(),
            despues=v.model_dump(),
        )
        return v.model_dump()

    @r.delete("/fincas/{finca_id}/viviendas/{vivienda_id}")
    async def borrar_vivienda(
        finca_id: str, vivienda_id: str, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        await _finca_o_404(finca_id)
        v = await _vivienda_o_404(finca_id, vivienda_id)
        await _sin_historial("vivienda_id", vivienda_id, f"la vivienda {v.etiqueta()}")
        await catalogo.eliminar_vivienda(vivienda_id)
        await auditar(
            actor, "vivienda_borrada", vivienda_id, finca_id=finca_id, antes=v.model_dump()
        )
        return {"borrada": vivienda_id}

    # --------------------------------------------------------------------------------- equipos

    @r.post("/fincas/{finca_id}/equipos", status_code=201)
    async def crear_equipo(
        finca_id: str, cuerpo: EquipoEntrada, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        f = await _finca_o_404(finca_id)
        await _mantenedor_valido(f, cuerpo.mantenedor_id)
        e = Equipo(id=_nuevo_id("eq", cuerpo.tipo), finca_id=finca_id, **cuerpo.model_dump())
        await catalogo.guardar_equipo(e)
        await auditar(
            actor, "equipo_creado", e.id, finca_id=finca_id, despues=e.model_dump(mode="json")
        )
        return _vista_equipo(e)

    @r.put("/fincas/{finca_id}/equipos/{equipo_id}")
    async def editar_equipo(
        finca_id: str, equipo_id: str, cuerpo: EquipoEntrada, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        f = await _finca_o_404(finca_id)
        antes = await _equipo_o_404(finca_id, equipo_id)
        await _mantenedor_valido(f, cuerpo.mantenedor_id)
        e = antes.model_copy(update=cuerpo.model_dump())
        await catalogo.guardar_equipo(e)
        await auditar(
            actor,
            "equipo_editado",
            e.id,
            finca_id=finca_id,
            antes=antes.model_dump(mode="json"),
            despues=e.model_dump(mode="json"),
        )
        return _vista_equipo(e)

    @r.delete("/fincas/{finca_id}/equipos/{equipo_id}")
    async def borrar_equipo(
        finca_id: str, equipo_id: str, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        await _finca_o_404(finca_id)
        e = await _equipo_o_404(finca_id, equipo_id)
        await _sin_historial("equipo_id", equipo_id, f"el equipo «{e.descripcion()}»")
        await catalogo.eliminar_equipo(equipo_id)
        await auditar(
            actor, "equipo_borrado", equipo_id, finca_id=finca_id, antes=e.model_dump(mode="json")
        )
        return {"borrado": equipo_id}

    # --------------------------------------------------------------------------- profesionales

    async def _categoria_valida(admin_id: str, categoria: str) -> None:
        if categoria not in await _catalogo_de(admin_id):
            raise HTTPException(422, f"La categoría «{categoria}» no está en el catálogo")

    @r.post("/especialistas", status_code=201)
    async def crear_especialista(
        cuerpo: EspecialistaNuevo, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        admin = await negocio.obtener_administrador(cuerpo.administrador_id)
        if admin is None:
            raise HTTPException(422, "Administrador desconocido")
        await _categoria_valida(admin.id, cuerpo.categoria_principal)
        e = Especialista(id=_nuevo_id("esp", cuerpo.nombre), **cuerpo.model_dump())
        await catalogo.guardar_especialista(e)
        await auditar(actor, "especialista_creado", e.id, despues=e.model_dump())
        return _vista_especialista(e, admin)

    @r.put("/especialistas/{especialista_id}")
    async def editar_especialista(
        especialista_id: str, cuerpo: EspecialistaEntrada, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        antes = await _especialista_o_404(especialista_id)
        await _categoria_valida(antes.administrador_id, cuerpo.categoria_principal)
        # El administrador no cambia: sus asignaciones y equipos son de ese administrador
        datos = cuerpo.model_dump(exclude={"administrador_id"})
        e = antes.model_copy(update=datos)
        await catalogo.guardar_especialista(e)
        await auditar(
            actor, "especialista_editado", e.id, antes=antes.model_dump(), despues=e.model_dump()
        )
        return _vista_especialista(e, await negocio.obtener_administrador(e.administrador_id))

    async def _usos_de(e: Especialista) -> tuple[list[tuple[str, str, str]], list[Equipo]]:
        """Dónde lo usa el asistente: (ámbito, id del ámbito, categoría) y equipos que mantiene."""
        categorias: list[tuple[str, str, str]] = []
        capa, _ = await catalogo.configuracion_de("administrador", e.administrador_id)
        for cat, esp in ((capa.especialista_por_categoria or {}) if capa else {}).items():
            if esp == e.id:
                categorias.append(("administrador", e.administrador_id, cat))
        equipos: list[Equipo] = []
        for f in await negocio.listar_fincas():
            if f.administrador_id != e.administrador_id:
                continue
            capa_f, _ = await catalogo.configuracion_de("finca", f.id)
            for cat, esp in ((capa_f.especialista_por_categoria or {}) if capa_f else {}).items():
                if esp == e.id:
                    categorias.append(("finca", f.id, cat))
            equipos += [q for q in await negocio.equipos_de(f.id) if q.mantenedor_id == e.id]
        return categorias, equipos

    @r.delete("/especialistas/{especialista_id}")
    async def borrar_especialista(
        especialista_id: str, forzar: bool = False, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        e = await _especialista_o_404(especialista_id)
        await _sin_historial("especialista_id", especialista_id, f"a {e.nombre}")
        categorias, equipos = await _usos_de(e)
        if (categorias or equipos) and not forzar:
            partes = []
            if categorias:
                nombres = sorted({c for _, _, c in categorias})
                partes.append("atiende " + ", ".join(nombres))
            if equipos:
                partes.append("mantiene " + ", ".join(q.descripcion() for q in equipos))
            raise HTTPException(
                409,
                f"{e.nombre} no se ha borrado porque el asistente lo usa: "
                f"{'; '.join(partes)}. Si se borra, esas averías quedarán para el "
                "administrador y los equipos sin mantenedor.",
            )

        versiones: list[str] = []
        for ambito, ambito_id in dict.fromkeys((a, i) for a, i, _ in categorias):
            capa, _ = await catalogo.configuracion_de(ambito, ambito_id)  # type: ignore[arg-type]
            assert capa is not None
            mapa = {c: x for c, x in (capa.especialista_por_categoria or {}).items() if x != e.id}
            nueva = capa.model_copy(update={"especialista_por_categoria": mapa})
            versiones.append(
                await catalogo.guardar_configuracion(ambito, ambito_id, nueva)  # type: ignore[arg-type]
            )
        for q in equipos:
            await catalogo.guardar_equipo(q.model_copy(update={"mantenedor_id": None}))
        await catalogo.eliminar_especialista(especialista_id)

        desasignadas = sorted({c for _, _, c in categorias})
        sin_mantenedor = [q.id for q in equipos]
        await auditar(
            actor,
            "especialista_borrado",
            especialista_id,
            antes=e.model_dump(),
            categorias_desasignadas=desasignadas,
            equipos_sin_mantenedor=sin_mantenedor,
            configuraciones=versiones,
        )
        return {
            "borrado": especialista_id,
            "categorias_desasignadas": desasignadas,
            "equipos_sin_mantenedor": sin_mantenedor,
        }

    # ------------------------------------------------------------ quién atiende cada categoría

    @r.put("/administradores/{admin_id}/categorias/{categoria}")
    async def asignar_categoria(
        admin_id: str, categoria: str, cuerpo: Asignacion, actor: Usuario = Depends(editor)
    ) -> dict[str, Any]:
        admin = await _admin_o_404(admin_id)
        await _categoria_valida(admin_id, categoria)
        if cuerpo.especialista_id is not None:
            e = await catalogo.obtener_especialista(cuerpo.especialista_id)
            if e is None or e.administrador_id != admin_id:
                raise HTTPException(422, "El profesional no es de este administrador")

        capa, _ = await catalogo.configuracion_de("administrador", admin_id)
        capa = capa or ConfiguracionParcial()
        mapa = dict(capa.especialista_por_categoria or {})
        anterior = mapa.get(categoria)
        if cuerpo.especialista_id is None:
            mapa.pop(categoria, None)
        else:
            mapa[categoria] = cuerpo.especialista_id
        if anterior != cuerpo.especialista_id:
            nueva = capa.model_copy(update={"especialista_por_categoria": mapa})
            version = await catalogo.guardar_configuracion("administrador", admin_id, nueva)
            await auditar(
                actor,
                "categoria_asignada",
                admin_id,
                categoria=categoria,
                antes=anterior,
                despues=cuerpo.especialista_id,
                configuracion_id=version,
            )
        return await _vista_administrador(admin)

    return r
