"""APIs protegidas de la consola operativa: resumen, expedientes, transcripción, decisiones,
auditoría y audio por URL firmada. Todo acceso queda auditado."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from building_call.acceso import Acceso
from building_call.audio_store import AlmacenAudio
from building_call.domain.operaciones import Rol, Usuario
from building_call.metricas import RegistroMetricas
from building_call.repos.operaciones import resumir_operaciones
from building_call.repos.operativo import RepositorioOperativo

REVISORES = (Rol.owner, Rol.administrador, Rol.calidad)


def crear_router_operaciones(
    repo: RepositorioOperativo,
    acceso: Acceso,
    metricas: RegistroMetricas,
    audio: AlmacenAudio | None,
) -> APIRouter:
    r = APIRouter(prefix="/api/operaciones")
    revisor = acceso.requiere(*REVISORES)

    async def auditar(usuario: Usuario, accion: str, recurso: str, **detalle: Any) -> None:
        await repo.registrar_auditoria(usuario.id, accion, recurso, detalle)

    def _latencia_p50(llamada_id: str) -> int | None:
        valores = [
            t.respuesta_ms for t in metricas.turnos(llamada_id) if t.respuesta_ms is not None
        ]
        if not valores:
            return None
        valores.sort()
        return valores[len(valores) // 2]

    @r.get("/resumen")
    async def resumen(usuario: Usuario = Depends(revisor)) -> dict[str, Any]:  # noqa: B008
        llamadas = await repo.listar_llamadas(limite=1000)
        filas = [
            {"resultado": ll.resultado, "respuesta_ms": _latencia_p50(ll.id)} for ll in llamadas
        ]
        agregados = resumir_operaciones(filas)
        agregados["activas"] = sum(1 for ll in llamadas if ll.estado == "activa")
        agregados["requieren_revision"] = sum(1 for ll in llamadas if ll.requiere_revision)
        agregados["latencia_global"] = metricas.agregados()["respuesta_ms"]
        return agregados

    @r.get("/llamadas")
    async def llamadas(
        estado: str | None = Query(default=None),
        limite: int = Query(default=100, le=500),
        usuario: Usuario = Depends(revisor),  # noqa: B008
    ) -> list[dict[str, Any]]:
        filas = await repo.listar_llamadas(estado=estado, limite=limite)
        return [
            {
                **ll.model_dump(mode="json", exclude={"transcripcion"}),
                "respuesta_ms_p50": _latencia_p50(ll.id),
            }
            for ll in filas
        ]

    @r.get("/llamadas/{llamada_id}")
    async def detalle(llamada_id: str, usuario: Usuario = Depends(revisor)) -> dict[str, Any]:  # noqa: B008
        ll = await repo.obtener_llamada(llamada_id)
        if ll is None:
            raise HTTPException(404, "Expediente no encontrado")
        await auditar(usuario, "ver_expediente", f"llamada:{llamada_id}")
        # La mezcla primero: es la que se escucha para seguir la conversación
        orden = {"mezcla": 0, "cliente": 1, "asistente": 2}
        pistas = sorted(
            await repo.listar_pistas(llamada_id), key=lambda p: orden.get(p["canal"], 9)
        )
        return {
            "llamada": ll.model_dump(mode="json"),
            "turnos": await repo.listar_turnos(llamada_id),
            "decisiones": await repo.listar_decisiones(llamada_id),
            "metricas": [t.como_dict() for t in metricas.turnos(llamada_id)],
            "pistas": [
                {
                    "id": p["id"],
                    "canal": p["canal"],
                    "duracion_ms": p.get("duracion_ms"),
                    "expira_en": p["expira_en"].isoformat()
                    if hasattr(p["expira_en"], "isoformat")
                    else p["expira_en"],
                    "disponible": not p.get("eliminada_en"),
                    # URL firmada de corta duración; requiere además sesión con rol revisor
                    "url": audio.url_firmada(p["id"])
                    if audio and not p.get("eliminada_en")
                    else None,
                }
                for p in pistas
            ],
        }

    @r.get("/llamadas/{llamada_id}/transcripcion")
    async def transcripcion(
        llamada_id: str, usuario: Usuario = Depends(revisor)
    ) -> list[dict[str, str]]:  # noqa: B008
        ll = await repo.obtener_llamada(llamada_id)
        if ll is None:
            raise HTTPException(404, "Expediente no encontrado")
        await auditar(usuario, "ver_transcripcion", f"llamada:{llamada_id}")
        return ll.transcripcion

    @r.get("/llamadas/{llamada_id}/decisiones")
    async def decisiones(
        llamada_id: str, usuario: Usuario = Depends(revisor)
    ) -> list[dict[str, Any]]:  # noqa: B008
        return await repo.listar_decisiones(llamada_id)

    @r.get("/auditoria")
    async def auditoria(
        limite: int = Query(default=200, le=1000),
        usuario: Usuario = Depends(acceso.requiere(Rol.owner, Rol.administrador)),  # noqa: B008
    ) -> list[dict[str, Any]]:
        return await repo.listar_auditoria(limite)

    @r.get("/audio/{pista_id}")
    async def audio_firmado(
        pista_id: str,
        exp: int = Query(...),
        firma: str = Query(...),
        usuario: Usuario = Depends(revisor),  # noqa: B008
    ) -> Response:
        if audio is None:
            raise HTTPException(503, "Almacén de audio no configurado")
        if not audio.firma_valida(pista_id, exp, firma):
            raise HTTPException(403, "URL de audio caducada o inválida")
        pista = await repo.obtener_pista(pista_id)
        if pista is None:
            raise HTTPException(404, "Pista no encontrada")
        datos = audio.leer(pista)
        if datos is None:
            raise HTTPException(410, "Pista eliminada por retención")
        await auditar(
            usuario, "reproducir_audio", f"pista:{pista_id}", llamada_id=pista["llamada_id"]
        )
        return Response(
            content=datos, media_type=pista["mime"], headers={"Cache-Control": "no-store"}
        )

    return r
