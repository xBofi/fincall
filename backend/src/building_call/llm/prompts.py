"""Prompts versionados de los nodos. Una sola versión por prompt; el identificador se registra
en las señales de confianza de cada llamada (auditoría). Cuando exista Langfuse, migran allí."""

from __future__ import annotations

from typing import Any

from building_call import i18n
from building_call.domain.config import UNCLASSIFIABLE_CATEGORY
from building_call.llm import prompts_en, wire

CLASSIFICATION_VERSION = "clasificacion-v6"

#: Qué entra en cada categoría del catálogo por defecto. Un administrador puede tener códigos
#: propios: sin descripción, el modelo solo ve el código. Provisional hasta validarlo.
CATEGORY_DESCRIPTION = {
    "plumbing": "agua, tuberías, grifos, desagües, atascos, fugas",
    # El portero automático no: lo atiende su instalador o mantenedor (2026-10-21)
    "electrical": "luces, bombillas, enchufes, interruptores, cuadro eléctrico, timbre",
    "elevator": "el ascensor: cabina, puertas, botones, ruidos",
    "cleaning": "suciedad, basura, cristales rotos en el suelo, olores de basura",
    "locksmith": "puertas que no abren o no cierran, cerraduras, llaves, mandos de garaje",
    "masonry": "paredes, techos, suelos, baldosas, grietas, humedades, fachada, cascotes",
    "painting": "pintura desconchada, pintadas",
    "gardening": "jardín, césped, árboles, setos, plantas",
    "administrative": "papeles: actas, juntas, certificados, recibos",
}


def described_catalog(catalog: list[str]) -> str:
    """'fontaneria (agua, tuberías…), electricidad (…), …' para los prompts."""
    return ", ".join(
        f"{wire.to_model(c)} ({CATEGORY_DESCRIPTION[c]})"
        if c in CATEGORY_DESCRIPTION
        else wire.to_model(c)
        for c in catalog
    )


def _spanish(language: str | None) -> bool:
    return i18n.get(language or i18n.active_code()).prompts == "es"


def classification_version(language: str | None = None) -> str:
    return CLASSIFICATION_VERSION if _spanish(language) else prompts_en.CLASSIFICATION_VERSION


def classification_system_prompt(
    catalog: list[str],
    equipment_list: list[tuple[str, str]] | None = None,
    language: str | None = None,
) -> str:
    """In the call's prompt set: Spanish, or English with a note on the caller's language."""
    code = language or i18n.active_code()
    if not _spanish(code):
        return prompts_en.classification_system_prompt(
            catalog, equipment_list
        ) + prompts_en.language_note(i18n.get(code).name)
    return _base_classification(catalog) + (
        " Si la incidencia afecta a uno de los equipos de esta finca, da su id en 'equipo', "
        f"exactamente uno de: {'; '.join(f'{i} ({d})' for i, d in equipment_list)}. Si nombra el tipo "
        "de equipo y en la finca solo hay uno de ese tipo, es ese."
        if equipment_list
        else ""
    )


def _base_classification(catalog: list[str]) -> str:
    unclassifiable = wire.to_model(UNCLASSIFIABLE_CATEGORY)
    return (
        "Eres el clasificador de incidencias de una comunidad de propietarios en España. "
        "Clasifica la solicitud del vecino en exactamente una categoría del catálogo cerrado: "
        f"{described_catalog(catalog)}. Si no encaja claramente en ninguna, usa "
        f"'{unclassifiable}'. La categoría la decide el elemento que falla, nunca "
        "el sitio donde está ni lo que tiene al lado: el enchufe junto al ascensor es "
        "electricidad y la grieta en la pared de los buzones es albanileria. Si no nombra el "
        "elemento que falla, no lo deduzcas del verbo aunque suela ir con uno ('se ha parado', "
        f"'no va', 'hace ruido' no dicen qué): usa '{unclassifiable}'. Si lo que falla "
        "no tiene categoría propia en el catálogo (gas, incendio, plagas…), usa "
        f"'{unclassifiable}', nunca la más parecida. "
        "Urgencia: baja (molestia sin riesgo ni daño), media "
        "(afecta al uso normal), alta (daño en curso, servicio esencial interrumpido o riesgo "
        "material), emergencia (riesgo inmediato para personas). Escribe un resumen de una frase "
        "corta en español, en tercera persona, con los datos que dice el vecino y sin inventar. "
        "Actuación: reparacion (arreglar algo estropeado), reposicion (cambiar algo entero o una "
        "obra nueva), siniestro (vandalismo, robo, tormenta, golpe de un tercero: va al seguro), "
        "convivencia (ruidos o conflictos entre vecinos) o privativo (dentro de una vivienda)."
    )


def classification_schema(catalog: list[str], language: str | None = None) -> dict[str, Any]:
    if not _spanish(language):
        return {
            "title": "classification",
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(catalog)},
                "urgency": {"type": "string", "enum": list(wire.URGENCIES.values())},
                "summary": {"type": "string"},
                "work_type": {"type": "string", "enum": list(wire.WORK_TYPES.values())},
                "equipment": {"type": "string"},
            },
            "required": ["category", "urgency", "summary", "work_type"],
        }
    return {
        "title": "classification",
        "type": "object",
        # Lo que ve el modelo: claves y códigos en español (`wire`)
        "properties": {
            "categoria": {"type": "string", "enum": [wire.to_model(c) for c in catalog]},
            "urgencia": {"type": "string", "enum": list(wire.URGENCIES)},
            "resumen": {"type": "string"},
            "actuacion": {"type": "string", "enum": list(wire.WORK_TYPES)},
            "equipo": {"type": "string"},
        },
        "required": ["categoria", "urgencia", "resumen", "actuacion"],
    }
