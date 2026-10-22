"""API del flujo del asistente: grafo, plantillas editables y prompts.

Dos capas, con permisos distintos a propósito:

- **Plantillas** (lo que el bot dice): las edita `owner`. Cada guardado se valida y se audita.
- **Prompts de sistema**: solo lectura. Declaran claves JSON y enums que el código valida;
  editarlos degrada exactitud y latencia en silencio (ver `graph/templates.py`).
- **Catálogos** (administradores, fincas, profesionales, equipos): en `catalog_api.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from building_call.access import Access
from building_call.domain.config import resolve
from building_call.domain.operations import Role, User
from building_call.graph import templates as tpl
from building_call.graph.manifest import MANIFEST
from building_call.graph.nodes import MANIFEST_VIOLATIONS
from building_call.llm.interpretation import (
    INTENTS,
    INTERPRETATION_VERSION,
    interpretation_system_prompt,
)
from building_call.llm.phrasing import PHRASING_SYSTEM, PHRASING_VERSION
from building_call.llm.prompts import CLASSIFICATION_VERSION, classification_system_prompt
from building_call.repos.base import Repository
from building_call.repos.operational import OperationalRepository

REVIEWERS = (Role.owner, Role.administrator, Role.quality)


class TemplateText(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _system_prompts(catalog: list[str]) -> list[dict[str, Any]]:
    """Los tres prompts, con el contrato que explica por qué no se editan aquí."""
    return [
        {
            "id": "interpretation",
            "title": "Interpretación del turno",
            "version": INTERPRETATION_VERSION,
            "file": "llm/interpretacion.py",
            "when": "Una vez por turno como máximo, y solo si ningún camino determinista basta.",
            "text": interpretation_system_prompt(catalog),
            "contract": [
                "Declara las claves JSON que `normalizar_interpretacion` mapea y `Interpretacion` "
                "valida: renombrar o quitar una hace que el dato deje de extraerse en silencio.",
                f"Las intenciones son un enum cerrado que el código comprueba: "
                f"{', '.join(INTENTS)}.",
                "El catálogo de categorías se inyecta desde la configuración de la finca; no "
                "está escrito en el prompt.",
                "El bloque de formato es latencia medida: con claves abreviadas la exactitud "
                "baja del 96% al 82%, y los nulos y saltos de línea triplican la latencia.",
            ],
        },
        {
            "id": "classification",
            "title": "Clasificación de la incidencia",
            "version": CLASSIFICATION_VERSION,
            "file": "llm/prompts.py",
            "when": "Respaldo, solo si la interpretación del turno no trajo ya la categoría.",
            "text": classification_system_prompt(catalog),
            "contract": [
                "La categoría y la urgencia se validan contra enums; lo que no encaje cae en "
                "`unclassifiable`.",
                "El catálogo es el de la finca, resuelto por capas.",
            ],
        },
        {
            "id": "phrasing",
            "title": "Redacción de la respuesta",
            "version": PHRASING_VERSION,
            "file": "llm/redaccion.py",
            "when": "En cada mensaje del bot, para parafrasear la plantilla con naturalidad.",
            "text": PHRASING_SYSTEM,
            "contract": [
                "`conserva_hechos()` comprueba en código que la paráfrasis mantiene números, "
                "referencias y nombres, y que conserva el interrogante.",
                "Si el contrato se rompe no hay error: se emite la plantilla tal cual y solo se "
                "nota en la métrica `redaccion: plantilla:hechos_no_conservados`.",
            ],
        },
    ]


def create_flow_router(
    repo: OperationalRepository, business: Repository, access: Access
) -> APIRouter:
    r = APIRouter(prefix="/api")
    reviewer = access.requires(*REVIEWERS)
    owner = access.requires(Role.owner)

    async def record_audit(user: User, action: str, resource: str, **detail: Any) -> None:
        await repo.record_audit(user.id, action, resource, detail)

    async def _category_catalog() -> list[str]:
        return resolve().category_catalog

    # ------------------------------------------------------------------------------- el grafo

    @r.get("/graph")
    async def graph(_: User = Depends(reviewer)) -> dict[str, Any]:
        """Manifiesto del flujo más las aristas realmente recorridas en llamadas guardadas."""
        traversed = await _real_edges()
        nodes = []
        for id_, n in MANIFEST.items():
            nodes.append(
                {
                    "id": id_,
                    "title": n.title,
                    "summary": n.summary,
                    "kind": n.kind,
                    "stage": n.stage,
                    "uses_llm": n.uses_llm,
                    "deterministic_paths": list(n.deterministic_paths),
                    "effects": list(n.effects),
                    "templates": [tid for tid, p in tpl.CATALOG.items() if p.node == id_],
                }
            )
        edges = [
            {
                "origin": id_,
                "target": a.target,
                "condition": a.condition,
                "times": traversed.get((id_, a.target), 0),
            }
            for id_, n in MANIFEST.items()
            for a in n.targets
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "utterance": "next_step",
            "global_templates": [tid for tid, p in tpl.CATALOG.items() if p.node == "global"],
            "violations": list(MANIFEST_VIOLATIONS),
        }

    async def _real_edges() -> dict[tuple[str, str], int]:
        """Transiciones observadas, de las decisiones ya guardadas por llamada."""
        counts: dict[tuple[str, str], int] = {}
        for call in await repo.list_calls(limit=200):
            decisions = await repo.list_decisions(call.id)
            nodes = [d.get("node") for d in sorted(decisions, key=lambda d: d.get("turn", 0))]
            for origin, target in zip(nodes, nodes[1:], strict=False):
                if origin and target and origin != target:
                    counts[(origin, target)] = counts.get((origin, target), 0) + 1
        return counts

    # -------------------------------------------------------------------------- los tres prompts

    @r.get("/prompts")
    async def prompts(_: User = Depends(reviewer)) -> dict[str, Any]:
        return {
            "editables": False,
            "reason": (
                "Los prompts de sistema declaran claves JSON y enums que el código valida. "
                "Editarlos desde aquí degradaría la exactitud y la latencia sin dar error. "
                "Lo que sí se edita es lo que el asistente dice: las plantillas de cada nodo."
            ),
            "prompts": _system_prompts(await _category_catalog()),
        }

    # ------------------------------------------------------------------------------- plantillas

    def _view(id_: str) -> dict[str, Any]:
        p = tpl.CATALOG[id_]
        return {
            "id": id_,
            "node": p.node,
            "description": p.description,
            "text": tpl.effective_text(id_),
            "original_text": p.text,
            "variables": list(p.variables),
            "question": p.question,
            "question_in": p.question_in,
            "overridden": tpl.is_overridden(id_),
            "version": tpl.effective_version(id_),
        }

    @r.get("/templates")
    async def list_templates(_: User = Depends(reviewer)) -> list[dict[str, Any]]:
        return [_view(id_) for id_ in tpl.CATALOG]

    @r.get("/templates/{template_id}")
    async def view_template(template_id: str, _: User = Depends(reviewer)) -> dict[str, Any]:
        if template_id not in tpl.CATALOG:
            raise HTTPException(404, "Plantilla desconocida")
        return {**_view(template_id), "history": await repo.template_history(template_id)}

    @r.put("/templates/{template_id}")
    async def publish_template(
        template_id: str, body: TemplateText, actor: User = Depends(owner)
    ) -> dict[str, Any]:
        if template_id not in tpl.CATALOG:
            raise HTTPException(404, "Plantilla desconocida")
        problems = tpl.validate(template_id, body.text)
        if problems:
            raise HTTPException(422, {"problems": problems})
        previous = tpl.effective_text(template_id)
        version = await repo.save_template(template_id, body.text.strip(), actor.id)
        tpl.apply(template_id, body.text, version)
        await record_audit(
            actor,
            "template_published",
            template_id,
            previous=previous,
            fresh=tpl.effective_text(template_id),
            version=version,
        )
        return _view(template_id)

    @r.delete("/templates/{template_id}")
    async def restore_template(
        template_id: str, actor: User = Depends(owner)
    ) -> dict[str, Any]:
        if template_id not in tpl.CATALOG:
            raise HTTPException(404, "Plantilla desconocida")
        previous = tpl.effective_text(template_id)
        await repo.delete_template(template_id)
        tpl.restore(template_id)
        await record_audit(actor, "template_restored", template_id, previous=previous)
        return _view(template_id)

    @r.post("/templates/{template_id}/validate")
    async def validate_template(
        template_id: str, body: TemplateText, _: User = Depends(owner)
    ) -> dict[str, Any]:
        """Previsualiza sin publicar: qué problemas tiene y qué hechos tendrán que sobrevivir."""
        if template_id not in tpl.CATALOG:
            raise HTTPException(404, "Plantilla desconocida")
        from building_call.llm.phrasing import required_facts

        return {
            "problems": tpl.validate(template_id, body.text),
            "required_facts": sorted(required_facts(body.text)),
        }

    return r
