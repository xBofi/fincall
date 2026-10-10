"""API del flujo del asistente: grafo, plantillas editables, prompts y catálogos.

Tres capas, con permisos distintos a propósito:

- **Plantillas** (lo que el bot dice): las edita `owner`. Cada guardado se valida y se audita.
- **Prompts de sistema**: solo lectura. Declaran claves JSON y enums que el código valida;
  editarlos degrada exactitud y latencia en silencio (ver `graph/plantillas.py`).
- **Catálogos** (administradores, fincas, profesionales): lectura para cualquier usuario de la
  consola, escritura para `owner`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from building_call.acceso import Acceso
from building_call.domain.configuracion import resolver
from building_call.domain.operaciones import Rol, Usuario
from building_call.graph import plantillas as pl
from building_call.graph.manifiesto import MANIFIESTO
from building_call.graph.nodos import VIOLACIONES_MANIFIESTO
from building_call.llm.interpretacion import (
    INTENCIONES,
    VERSION_INTERPRETACION,
    sistema_interpretacion,
)
from building_call.llm.prompts import VERSION_CLASIFICACION, sistema_clasificacion
from building_call.llm.redaccion import SISTEMA_REDACCION, VERSION_REDACCION
from building_call.repos.base import Repositorio
from building_call.repos.operativo import RepositorioOperativo

REVISORES = (Rol.owner, Rol.administrador, Rol.calidad)


class TextoPlantilla(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)


def _prompts_de_sistema(catalogo: list[str]) -> list[dict[str, Any]]:
    """Los tres prompts, con el contrato que explica por qué no se editan aquí."""
    return [
        {
            "id": "interpretacion",
            "titulo": "Interpretación del turno",
            "version": VERSION_INTERPRETACION,
            "fichero": "llm/interpretacion.py",
            "cuando": "Una vez por turno como máximo, y solo si ningún camino determinista basta.",
            "texto": sistema_interpretacion(catalogo),
            "contrato": [
                "Declara las claves JSON que `normalizar_interpretacion` mapea y `Interpretacion` "
                "valida: renombrar o quitar una hace que el dato deje de extraerse en silencio.",
                f"Las intenciones son un enum cerrado que el código comprueba: "
                f"{', '.join(INTENCIONES)}.",
                "El catálogo de categorías se inyecta desde la configuración de la finca; no "
                "está escrito en el prompt.",
                "El bloque de formato es latencia medida: con claves abreviadas la exactitud "
                "baja del 96% al 82%, y los nulos y saltos de línea triplican la latencia.",
            ],
        },
        {
            "id": "clasificacion",
            "titulo": "Clasificación de la incidencia",
            "version": VERSION_CLASIFICACION,
            "fichero": "llm/prompts.py",
            "cuando": "Respaldo, solo si la interpretación del turno no trajo ya la categoría.",
            "texto": sistema_clasificacion(catalogo),
            "contrato": [
                "La categoría y la urgencia se validan contra enums; lo que no encaje cae en "
                "`no_clasificable`.",
                "El catálogo es el de la finca, resuelto por capas.",
            ],
        },
        {
            "id": "redaccion",
            "titulo": "Redacción de la respuesta",
            "version": VERSION_REDACCION,
            "fichero": "llm/redaccion.py",
            "cuando": "En cada mensaje del bot, para parafrasear la plantilla con naturalidad.",
            "texto": SISTEMA_REDACCION,
            "contrato": [
                "`conserva_hechos()` comprueba en código que la paráfrasis mantiene números, "
                "referencias y nombres, y que conserva el interrogante.",
                "Si el contrato se rompe no hay error: se emite la plantilla tal cual y solo se "
                "nota en la métrica `redaccion: plantilla:hechos_no_conservados`.",
            ],
        },
    ]


def crear_router_flujo(
    repo: RepositorioOperativo, negocio: Repositorio, acceso: Acceso
) -> APIRouter:
    r = APIRouter(prefix="/api")
    revisor = acceso.requiere(*REVISORES)
    owner = acceso.requiere(Rol.owner)

    async def auditar(usuario: Usuario, accion: str, recurso: str, **detalle: Any) -> None:
        await repo.registrar_auditoria(usuario.id, accion, recurso, detalle)

    async def _catalogo_categorias() -> list[str]:
        return resolver().catalogo_categorias

    # ------------------------------------------------------------------------------- el grafo

    @r.get("/grafo")
    async def grafo(_: Usuario = Depends(revisor)) -> dict[str, Any]:
        """Manifiesto del flujo más las aristas realmente recorridas en llamadas guardadas."""
        recorridas = await _aristas_reales()
        nodos = []
        for id_, n in MANIFIESTO.items():
            nodos.append(
                {
                    "id": id_,
                    "titulo": n.titulo,
                    "resumen": n.resumen,
                    "tipo": n.tipo,
                    "etapa": n.etapa,
                    "usa_llm": n.usa_llm,
                    "caminos_deterministas": list(n.caminos_deterministas),
                    "efectos": list(n.efectos),
                    "plantillas": [
                        pid for pid, p in pl.CATALOGO.items() if p.nodo == id_
                    ],
                }
            )
        aristas = [
            {
                "origen": id_,
                "destino": a.destino,
                "condicion": a.condicion,
                "veces": recorridas.get((id_, a.destino), 0),
            }
            for id_, n in MANIFIESTO.items()
            for a in n.destinos
        ]
        return {
            "nodos": nodos,
            "aristas": aristas,
            "entrada": "siguiente_paso",
            "plantillas_globales": [
                pid for pid, p in pl.CATALOGO.items() if p.nodo == "global"
            ],
            "violaciones": list(VIOLACIONES_MANIFIESTO),
        }

    async def _aristas_reales() -> dict[tuple[str, str], int]:
        """Transiciones observadas, de las decisiones ya guardadas por llamada."""
        cuenta: dict[tuple[str, str], int] = {}
        for llamada in await repo.listar_llamadas(limite=200):
            decisiones = await repo.listar_decisiones(llamada.id)
            nodos = [d.get("nodo") for d in sorted(decisiones, key=lambda d: d.get("turno", 0))]
            for origen, destino in zip(nodos, nodos[1:], strict=False):
                if origen and destino and origen != destino:
                    cuenta[(origen, destino)] = cuenta.get((origen, destino), 0) + 1
        return cuenta

    # -------------------------------------------------------------------------- los tres prompts

    @r.get("/prompts")
    async def prompts(_: Usuario = Depends(revisor)) -> dict[str, Any]:
        return {
            "editables": False,
            "motivo": (
                "Los prompts de sistema declaran claves JSON y enums que el código valida. "
                "Editarlos desde aquí degradaría la exactitud y la latencia sin dar error. "
                "Lo que sí se edita es lo que el asistente dice: las plantillas de cada nodo."
            ),
            "prompts": _prompts_de_sistema(await _catalogo_categorias()),
        }

    # ------------------------------------------------------------------------------- plantillas

    def _vista(id_: str) -> dict[str, Any]:
        p = pl.CATALOGO[id_]
        return {
            "id": id_,
            "nodo": p.nodo,
            "descripcion": p.descripcion,
            "texto": pl.texto_efectivo(id_),
            "texto_original": p.texto,
            "variables": list(p.variables),
            "pregunta": p.pregunta,
            "pregunta_en": p.pregunta_en,
            "sobrescrita": pl.esta_sobrescrita(id_),
            "version": pl.version_efectiva(id_),
        }

    @r.get("/plantillas")
    async def listar_plantillas(_: Usuario = Depends(revisor)) -> list[dict[str, Any]]:
        return [_vista(id_) for id_ in pl.CATALOGO]

    @r.get("/plantillas/{plantilla_id}")
    async def ver_plantilla(
        plantilla_id: str, _: Usuario = Depends(revisor)
    ) -> dict[str, Any]:
        if plantilla_id not in pl.CATALOGO:
            raise HTTPException(404, "Plantilla desconocida")
        return {**_vista(plantilla_id), "historial": await repo.historial_plantilla(plantilla_id)}

    @r.put("/plantillas/{plantilla_id}")
    async def publicar_plantilla(
        plantilla_id: str, cuerpo: TextoPlantilla, actor: Usuario = Depends(owner)
    ) -> dict[str, Any]:
        if plantilla_id not in pl.CATALOGO:
            raise HTTPException(404, "Plantilla desconocida")
        problemas = pl.validar(plantilla_id, cuerpo.texto)
        if problemas:
            raise HTTPException(422, {"problemas": problemas})
        anterior = pl.texto_efectivo(plantilla_id)
        version = await repo.guardar_plantilla(plantilla_id, cuerpo.texto.strip(), actor.id)
        pl.aplicar(plantilla_id, cuerpo.texto, version)
        await auditar(
            actor,
            "plantilla_publicada",
            plantilla_id,
            anterior=anterior,
            nuevo=pl.texto_efectivo(plantilla_id),
            version=version,
        )
        return _vista(plantilla_id)

    @r.delete("/plantillas/{plantilla_id}")
    async def restaurar_plantilla(
        plantilla_id: str, actor: Usuario = Depends(owner)
    ) -> dict[str, Any]:
        if plantilla_id not in pl.CATALOGO:
            raise HTTPException(404, "Plantilla desconocida")
        anterior = pl.texto_efectivo(plantilla_id)
        await repo.borrar_plantilla(plantilla_id)
        pl.restaurar(plantilla_id)
        await auditar(actor, "plantilla_restaurada", plantilla_id, anterior=anterior)
        return _vista(plantilla_id)

    @r.post("/plantillas/{plantilla_id}/validar")
    async def validar_plantilla(
        plantilla_id: str, cuerpo: TextoPlantilla, _: Usuario = Depends(owner)
    ) -> dict[str, Any]:
        """Previsualiza sin publicar: qué problemas tiene y qué hechos tendrán que sobrevivir."""
        if plantilla_id not in pl.CATALOGO:
            raise HTTPException(404, "Plantilla desconocida")
        from building_call.llm.redaccion import hechos_obligatorios

        return {
            "problemas": pl.validar(plantilla_id, cuerpo.texto),
            "hechos_obligatorios": sorted(hechos_obligatorios(cuerpo.texto)),
        }

    # -------------------------------------------------------------------------------- catálogos

    @r.get("/catalogos/administradores")
    async def administradores(_: Usuario = Depends(revisor)) -> list[dict[str, Any]]:
        """Los administradores se derivan de las fincas: no hay listado propio en el repositorio."""
        fincas = await negocio.listar_fincas()
        salida = []
        for admin_id in dict.fromkeys(f.administrador_id for f in fincas):
            admin = await negocio.obtener_administrador(admin_id)
            especialistas = await negocio.especialistas_de(admin_id)
            salida.append(
                {
                    "id": admin_id,
                    "nombre": admin.nombre if admin else admin_id,
                    "fincas": [
                        {"id": f.id, "direccion": f.direccion}
                        for f in fincas
                        if f.administrador_id == admin_id
                    ],
                    "especialistas": [
                        {
                            "id": e.id,
                            "nombre": e.nombre,
                            "categoria_principal": e.categoria_principal,
                            "canal_contacto": e.canal_contacto,
                            "destino": e.destino,
                        }
                        for e in especialistas
                    ],
                }
            )
        return salida

    @r.get("/catalogos/fincas")
    async def fincas(_: Usuario = Depends(revisor)) -> list[dict[str, Any]]:
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

    @r.get("/catalogos/fincas/{finca_id}")
    async def finca(finca_id: str, _: Usuario = Depends(revisor)) -> dict[str, Any]:
        f = await negocio.obtener_finca(finca_id)
        if f is None:
            raise HTTPException(404, "Finca desconocida")
        admin = await negocio.obtener_administrador(f.administrador_id)
        viviendas = await negocio.viviendas_de(finca_id)
        cfg, cfg_id = await negocio.configuracion_efectiva(finca_id)
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
            # Las tres capas ya resueltas: la forma más rápida de entender por qué una finca se
            # comporta distinto de otra
            "configuracion_efectiva": cfg.model_dump(),
            "configuracion_id": cfg_id,
        }

    @r.get("/catalogos/especialistas")
    async def especialistas(_: Usuario = Depends(revisor)) -> list[dict[str, Any]]:
        fincas = await negocio.listar_fincas()
        salida = []
        for admin_id in dict.fromkeys(f.administrador_id for f in fincas):
            admin = await negocio.obtener_administrador(admin_id)
            for e in await negocio.especialistas_de(admin_id):
                salida.append(
                    {
                        "id": e.id,
                        "nombre": e.nombre,
                        "administrador_id": admin_id,
                        "administrador": admin.nombre if admin else admin_id,
                        "categoria_principal": e.categoria_principal,
                        "canal_contacto": e.canal_contacto,
                        "destino": e.destino,
                    }
                )
        return salida

    return r
