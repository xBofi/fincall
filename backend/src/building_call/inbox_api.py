"""Bandeja del administrador: incidencias que esperan su decisión y lo que hace con ellas.

Con las reglas de enrutamiento (2026-10-21) muchas incidencias llegan sin especialista y en
estado `pending`, con una sugerencia y un motivo. Aquí se listan, se ven con su llamada y se
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

from building_call.access import Access
from building_call.domain.config import URGENCIES, Urgency
from building_call.domain.entities import (
    Incident,
    IncidentAction,
    IncidentHistoryEntry,
    IncidentStatus,
)
from building_call.domain.operations import Role, User
from building_call.repos.base import Repository
from building_call.repos.operational import OperationalRepository

READERS = (Role.owner, Role.administrator, Role.quality)
MANAGERS = (Role.owner, Role.administrator)

#: Estados a los que se llega a mano. `assigned` solo se alcanza asignando a un especialista.
ManualStatus = Literal["in_progress", "awaiting_information", "resolved", "closed"]
#: Estado final: una incidencia cerrada ya no se toca
FINAL_STATUSES = {"closed"}
#: Lo resuelto ya no se reasigna: si vuelve a fallar, primero se reabre (en curso)
NOT_ASSIGNABLE = {"resolved", "closed"}
PENDING_NOTIFICATION = "pending_send"

_URGENCY_WEIGHT = {u: i for i, u in enumerate(reversed(URGENCIES))}  # emergencia = 0


class AssignRequest(BaseModel):
    specialist_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


class StatusRequest(BaseModel):
    state: ManualStatus
    note: str | None = Field(default=None, max_length=2000)


class NoteRequest(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La nota no puede estar vacía")
        return value


def pending_administrator(inc: Incident) -> bool:
    return inc.specialist_id is None and inc.state == "pending"


def _clean_note(note: str | None) -> str | None:
    return (note or "").strip() or None


def create_inbox_router(
    repo: Repository, operational: OperationalRepository, access: Access
) -> APIRouter:
    r = APIRouter(prefix="/api/inbox")
    reader = access.requires(*READERS)
    manager = access.requires(*MANAGERS)

    async def _incident(incident_id: str) -> Incident:
        inc = await repo.get_incident(incident_id)
        if inc is None:
            raise HTTPException(404, "Incidencia no encontrada")
        return inc

    async def _specialists(estate_id: str) -> dict[str, Any]:
        estate = await repo.get_estate(estate_id)
        if estate is None:
            return {}
        return {e.id: e for e in await repo.specialists_of(estate.administrator_id)}

    async def _record(
        user: User,
        before: Incident,
        after: Incident,
        action: IncidentAction,
        note: str | None,
        **detail: Any,
    ) -> IncidentHistoryEntry:
        utterance = IncidentHistoryEntry(
            id=f"hist-{uuid4().hex[:16]}",
            incident_id=before.id,
            action=action,
            previous_state=before.state,
            new_state=after.state,
            user_id=user.id,
            note=note,
            detail=detail,
            changed_at=datetime.now(UTC),
        )
        # Incidencia e historial en la misma transacción; si otro usuario actuó mientras tanto,
        # no se pisa su decisión
        if not await repo.apply_incident_action(before, after, utterance):
            raise HTTPException(409, "La incidencia ha cambiado mientras tanto: vuelve a cargarla")
        await operational.record_audit(
            user.id,
            {
                "assign": "assign_incident",
                "change_state": "change_incident_state",
                "note": "incident_note",
            }[action],
            f"incident:{before.id}",
            {
                "previous_state": before.state,
                "new_state": after.state,
                **({"note": note} if note else {}),
                **detail,
            },
        )
        return utterance

    async def _response(inc: Incident, utterance: IncidentHistoryEntry) -> dict[str, Any]:
        return {
            "incident": inc.model_dump(mode="json"),
            "history": utterance.model_dump(mode="json"),
            **({"notice": utterance.detail["notice"]} if "notice" in utterance.detail else {}),
        }

    @r.get("/incidents")
    async def list_all(
        state: IncidentStatus | None = None,
        pending_administrator_only: bool = False,
        estate_id: str | None = None,
        urgency: Urgency | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
        _: User = Depends(reader),  # noqa: B008
    ) -> list[dict[str, Any]]:
        rows = [
            i
            for i in await repo.list_incidents()
            if (state is None or i.state == state)
            and (not pending_administrator_only or pending_administrator(i))
            and (estate_id is None or i.estate_id == estate_id)
            and (urgency is None or i.urgency == urgency)
        ]
        # Lo que espera al administrador primero; dentro, lo más urgente y lo más reciente
        rows.sort(key=lambda i: i.created_at, reverse=True)
        rows.sort(key=lambda i: (not pending_administrator(i), _URGENCY_WEIGHT[i.urgency]))
        rows = rows[:limit]

        estates = {f.id: f for f in await repo.list_estates()}
        homes: dict[str, str] = {}
        specialists: dict[str, str] = {}
        for eid in {i.estate_id for i in rows}:
            homes.update({v.id: v.label() for v in await repo.homes_of(eid)})
            if eid in estates:
                specialists.update(
                    {
                        e.id: e.name
                        for e in await repo.specialists_of(estates[eid].administrator_id)
                    }
                )
        output = []
        for i in rows:
            call = await operational.get_call(i.call_id)
            estate = estates.get(i.estate_id)
            output.append(
                {
                    **i.model_dump(mode="json"),
                    "pending_administrator": pending_administrator(i),
                    "estate_address": estate.address if estate else None,
                    "home": homes.get(i.home_id),
                    "specialist_name": specialists.get(i.specialist_id or ""),
                    "review_reason": call.review_reason if call else None,
                }
            )
        return output

    @r.get("/incidents/{incident_id}")
    async def detail(
        incident_id: str,
        user: User = Depends(reader),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incident(incident_id)
        await operational.record_audit(
            user.id, "view_incident", f"incident:{incident_id}", {}
        )
        estate = await repo.get_estate(inc.estate_id)
        specialists = await _specialists(inc.estate_id)
        home = next(
            (v for v in await repo.homes_of(inc.estate_id) if v.id == inc.home_id), None
        )
        equipment = next(
            (e for e in await repo.equipment_of(inc.estate_id) if e.id == inc.equipment_id), None
        )
        call = await operational.get_call(inc.call_id)
        assigned = specialists.get(inc.specialist_id or "")
        return {
            "incident": inc.model_dump(mode="json"),
            "pending_administrator": pending_administrator(inc),
            "estate": estate.model_dump(mode="json") if estate else None,
            "home": home.label() if home else None,
            "call": call.model_dump(
                mode="json",
                include={
                    "id",
                    "started_at",
                    "caller_phone",
                    "declared_name",
                    "summary",
                    "transcript",
                    "result",
                    "review_reason",
                },
            )
            if call
            else None,
            "equipment": {
                **equipment.model_dump(mode="json"),
                "description": equipment.description(),
                "maintainer": (
                    specialists[equipment.maintainer_id].model_dump(mode="json")
                    if equipment.maintainer_id in specialists
                    else None
                ),
            }
            if equipment
            else None,
            "specialist": assigned.model_dump(mode="json") if assigned else None,
            "specialists": [e.model_dump(mode="json") for e in specialists.values()],
            "history": [
                {**h.model_dump(mode="json"), "user_name": await _name(h.user_id)}
                for h in await repo.list_incident_history(inc.id)
            ],
        }

    async def _name(user_id: str | None) -> str | None:
        if user_id is None:
            return None
        u = await operational.get_user(user_id)
        return (u.name or u.username) if u else None

    @r.post("/incidents/{incident_id}/assign")
    async def assign(
        incident_id: str,
        request_body: AssignRequest,
        user: User = Depends(manager),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incident(incident_id)
        if inc.state in NOT_ASSIGNABLE:
            raise HTTPException(409, f"Una incidencia {inc.state} no se asigna")
        specialist = (await _specialists(inc.estate_id)).get(request_body.specialist_id)
        if specialist is None:
            raise HTTPException(422, "El especialista no pertenece al administrador de la finca")
        updated = inc.model_copy(update={"specialist_id": specialist.id, "state": "assigned"})
        # Sin avisos reales todavía: el aviso queda anotado como pendiente de envío
        utterance = await _record(
            user,
            inc,
            updated,
            "assign",
            _clean_note(request_body.note),
            specialist_id=specialist.id,
            specialist_name=specialist.name,
            previous_specialist=inc.specialist_id,
            channel=specialist.contact_channel,
            notice=PENDING_NOTIFICATION,
        )
        return await _response(updated, utterance)

    @r.post("/incidents/{incident_id}/state")
    async def change_state(
        incident_id: str,
        request_body: StatusRequest,
        user: User = Depends(manager),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incident(incident_id)
        if inc.state in FINAL_STATUSES:
            raise HTTPException(409, "La incidencia está cerrada")
        if request_body.state == inc.state:
            raise HTTPException(409, f"La incidencia ya está {inc.state}")
        updated = inc.model_copy(update={"state": request_body.state})
        utterance = await _record(user, inc, updated, "change_state", _clean_note(request_body.note))
        return await _response(updated, utterance)

    @r.post("/incidents/{incident_id}/note")
    async def annotate(
        incident_id: str,
        request_body: NoteRequest,
        user: User = Depends(manager),  # noqa: B008
    ) -> dict[str, Any]:
        inc = await _incident(incident_id)
        utterance = await _record(user, inc, inc, "note", request_body.text)
        return await _response(inc, utterance)

    return r
