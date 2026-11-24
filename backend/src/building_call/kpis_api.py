"""Conversation KPIs for the console: `GET /api/operations/kpis` (aggregated, last N calls)
and `GET /api/operations/calls/{id}/kpis`. Same roles as the rest of the operational console;
the answer has no personal data (no transcript, no phone), only counts and timings."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from building_call.access import Access
from building_call.domain.entities import Call
from building_call.domain.operations import User
from building_call.kpis import aggregate, call_kpis
from building_call.metrics import MetricsRegistry
from building_call.operations_api import REVIEWERS
from building_call.repos.operational import OperationalRepository


def create_kpis_router(
    repo: OperationalRepository, access: Access, metrics: MetricsRegistry
) -> APIRouter:
    r = APIRouter(prefix="/api/operations")
    reviewer = access.requires(*REVIEWERS)

    def _response_ms(call_id: str) -> list[float]:
        return [float(t.response_ms) for t in metrics.turns(call_id) if t.response_ms is not None]

    async def _kpis(call: Call) -> dict[str, Any]:
        turns, decisions = await asyncio.gather(
            repo.list_turns(call.id), repo.list_decisions(call.id)
        )
        return call_kpis(call, turns, decisions, _response_ms(call.id))

    @r.get("/kpis")
    async def kpis(
        limit: int = Query(default=200, ge=1, le=1000),
        user: User = Depends(reviewer),  # noqa: B008
    ) -> dict[str, Any]:
        calls = await repo.list_calls(limit=limit)
        per_call = [await _kpis(call) for call in calls]
        return {"aggregate": aggregate(per_call), "window": limit}

    @r.get("/calls/{call_id}/kpis")
    async def call(call_id: str, user: User = Depends(reviewer)) -> dict[str, Any]:  # noqa: B008
        found = await repo.get_call(call_id)
        if found is None:
            raise HTTPException(404, "Expediente no encontrado")
        return await _kpis(found)

    return r
