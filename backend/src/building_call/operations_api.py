"""APIs protegidas de la consola operativa: resumen, expedientes, transcripción, decisiones,
auditoría, audio por URL firmada y casos del banco de pruebas. Todo acceso queda auditado."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from building_call.access import Access
from building_call.audio_store import AudioStore
from building_call.bank_cases import (
    EXPECTED_KEYS,
    BankCaseInput,
    InvalidCase,
    build_case,
)
from building_call.domain.config import DEFAULTS, UNCLASSIFIABLE_CATEGORY, URGENCIES
from building_call.domain.entities import WORK_TYPES
from building_call.domain.operations import Role, User
from building_call.llm.interpretation import INTENTS
from building_call.metrics import MetricsRegistry
from building_call.repos.operational import OperationalRepository
from building_call.repos.operations import summarize_operations

REVIEWERS = (Role.owner, Role.administrator, Role.quality)


def create_operations_router(
    repo: OperationalRepository,
    access: Access,
    metrics: MetricsRegistry,
    audio: AudioStore | None,
) -> APIRouter:
    r = APIRouter(prefix="/api/operations")
    reviewer = access.requires(*REVIEWERS)

    async def record_audit(user: User, action: str, resource: str, **detail: Any) -> None:
        await repo.record_audit(user.id, action, resource, detail)

    def _p50_latencies() -> dict[str, int]:
        """Mediana de `response_ms` por llamada, en una sola pasada por el registro."""
        by_call: dict[str, list[int]] = {}
        for t in metrics.turns():
            if t.response_ms is not None:
                by_call.setdefault(t.call_id, []).append(t.response_ms)
        return {k: sorted(v)[len(v) // 2] for k, v in by_call.items()}

    @r.get("/summary")
    async def summary(user: User = Depends(reviewer)) -> dict[str, Any]:  # noqa: B008
        calls = await repo.list_calls(limit=1000)
        p50 = _p50_latencies()
        rows = [{"result": call.result, "response_ms": p50.get(call.id)} for call in calls]
        aggregates = summarize_operations(rows)
        aggregates["active_calls"] = sum(1 for call in calls if call.state == "active")
        aggregates["need_review"] = sum(1 for call in calls if call.needs_review)
        aggregates["global_latency"] = metrics.aggregates()["response_ms"]
        return aggregates

    @r.get("/calls")
    async def calls(
        state: str | None = Query(default=None),
        limit: int = Query(default=100, le=500),
        user: User = Depends(reviewer),  # noqa: B008
    ) -> list[dict[str, Any]]:
        rows = await repo.list_calls(state=state, limit=limit)
        p50 = _p50_latencies()
        return [
            {
                **call.model_dump(mode="json", exclude={"transcript"}),
                "response_ms_p50": p50.get(call.id),
            }
            for call in rows
        ]

    @r.get("/calls/{call_id}")
    async def detail(call_id: str, user: User = Depends(reviewer)) -> dict[str, Any]:  # noqa: B008
        call = await repo.get_call(call_id)
        if call is None:
            raise HTTPException(404, "Expediente no encontrado")
        await record_audit(user, "view_call_record", f"call:{call_id}")
        # La mezcla primero: es la que se escucha para seguir la conversación
        order = {"mix": 0, "client": 1, "assistant": 2}
        tracks = sorted(
            await repo.list_tracks(call_id), key=lambda p: order.get(p["channel"], 9)
        )
        return {
            "call": call.model_dump(mode="json"),
            "turns": await repo.list_turns(call_id),
            "decisions": await repo.list_decisions(call_id),
            "cases": await repo.list_test_bank_cases(call_id),
            "metrics": [t.as_dict() for t in metrics.turns(call_id)],
            "tracks": [
                {
                    "id": p["id"],
                    "channel": p["channel"],
                    "duration_ms": p.get("duration_ms"),
                    "expires_at": p["expires_at"].isoformat()
                    if hasattr(p["expires_at"], "isoformat")
                    else p["expires_at"],
                    "available": not p.get("deleted_at"),
                    # URL firmada de corta duración; requiere además sesión con rol revisor
                    "url": audio.signed_url(p["id"])
                    if audio and not p.get("deleted_at")
                    else None,
                }
                for p in tracks
            ],
        }

    @r.get("/calls/{call_id}/transcript")
    async def transcript(
        call_id: str, user: User = Depends(reviewer)
    ) -> list[dict[str, str]]:  # noqa: B008
        call = await repo.get_call(call_id)
        if call is None:
            raise HTTPException(404, "Expediente no encontrado")
        await record_audit(user, "view_transcript", f"call:{call_id}")
        return call.transcript

    @r.get("/audit")
    async def audit(
        limit: int = Query(default=200, le=1000),
        user: User = Depends(access.requires(Role.owner, Role.administrator)),  # noqa: B008
    ) -> list[dict[str, Any]]:
        return await repo.list_audit(limit)

    @r.get("/audio/{track_id}")
    async def signed_audio(
        track_id: str,
        exp: int = Query(...),
        signature: str = Query(...),
        user: User = Depends(reviewer),  # noqa: B008
    ) -> Response:
        if audio is None:
            raise HTTPException(503, "Almacén de audio no configurado")
        if not audio.valid_signature(track_id, exp, signature):
            raise HTTPException(403, "URL de audio caducada o inválida")
        track = await repo.get_track(track_id)
        if track is None:
            raise HTTPException(404, "Pista no encontrada")
        data = audio.read(track)
        if data is None:
            raise HTTPException(410, "Pista eliminada por retención")
        await record_audit(
            user, "play_audio", f"track:{track_id}", call_id=track["call_id"]
        )
        return Response(
            content=data, media_type=track["mime"], headers={"Cache-Control": "no-store"}
        )

    # ------------------------------------------------------------- casos del banco de pruebas

    @r.get("/cases/options")
    async def case_options(user: User = Depends(reviewer)) -> dict[str, Any]:  # noqa: B008
        """Valores válidos para rellenar lo que el asistente debería haber entendido."""
        return {
            "keys": list(EXPECTED_KEYS),
            "intents": list(INTENTS),
            "urgencies": list(URGENCIES),
            "work_types": list(WORK_TYPES),
            "categories": [*DEFAULTS.category_catalog, UNCLASSIFIABLE_CATEGORY],
        }

    @r.get("/cases")
    async def cases(user: User = Depends(reviewer)) -> list[dict[str, Any]]:  # noqa: B008
        """Todos los casos marcados: es lo que `evals/export_cases.py --entrada` lee."""
        return await repo.list_test_bank_cases()

    @r.get("/calls/{call_id}/cases")
    async def call_cases(
        call_id: str,
        user: User = Depends(reviewer),  # noqa: B008
    ) -> list[dict[str, Any]]:
        return await repo.list_test_bank_cases(call_id)

    @r.post("/calls/{call_id}/cases", status_code=201)
    async def mark_case(
        call_id: str,
        body: BankCaseInput,
        user: User = Depends(reviewer),  # noqa: B008
    ) -> dict[str, Any]:
        call = await repo.get_call(call_id)
        if call is None:
            raise HTTPException(404, "Expediente no encontrado")
        equipment_list = None
        if call.estate_id and hasattr(repo, "equipment_of"):
            equipment_list = [(e.id, e.description()) for e in await repo.equipment_of(call.estate_id)]
        try:
            fields = build_case(
                body,
                await repo.list_turns(call_id),
                real_name=call.declared_name,
                equipment_list=equipment_list,
            )
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except InvalidCase as e:
            raise HTTPException(422, str(e)) from e
        case = await repo.save_test_bank_case(
            {**fields, "call_id": call_id, "author_id": user.id}
        )
        await record_audit(
            user,
            "mark_test_bank_case",
            f"call:{call_id}",
            turn=body.turn,
            case_id=case["id"],
            bank=body.bank,
        )
        return case

    @r.delete("/cases/{case_id}", status_code=204)
    async def unmark_case(case_id: str, user: User = Depends(reviewer)) -> Response:  # noqa: B008
        case = await repo.delete_test_bank_case(case_id)
        if case is None:
            raise HTTPException(404, "Caso no encontrado")
        await record_audit(
            user,
            "unmark_test_bank_case",
            f"call:{case['call_id']}",
            turn=case["turn"],
            case_id=case_id,
        )
        return Response(status_code=204)

    return r
