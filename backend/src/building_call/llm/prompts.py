"""Prompts versionados de los nodos. Una sola versión por prompt; el identificador se registra
en las señales de confianza de cada llamada (auditoría). Cuando exista Langfuse, migran allí."""

from __future__ import annotations

from typing import Any

from building_call.domain.configuracion import CATEGORIA_NO_CLASIFICABLE, URGENCIAS

VERSION_CLASIFICACION = "clasificacion-v5"

#: Qué entra en cada categoría del catálogo por defecto. Un administrador puede tener códigos
#: propios: sin descripción, el modelo solo ve el código. Provisional hasta que Andreu lo valide.
DESCRIPCION_CATEGORIA = {
    "fontaneria": "agua, tuberías, grifos, desagües, atascos, fugas",
    "electricidad": "luces, bombillas, enchufes, interruptores, cuadro eléctrico, portero "
    "automático, timbre",
    "ascensor": "el ascensor: cabina, puertas, botones, ruidos",
    "limpieza": "suciedad, basura, cristales rotos en el suelo, olores de basura",
    "cerrajeria": "puertas que no abren o no cierran, cerraduras, llaves, mandos de garaje",
    "albanileria": "paredes, techos, suelos, baldosas, grietas, humedades, fachada, cascotes",
    "pintura": "pintura desconchada, pintadas",
    "jardineria": "jardín, césped, árboles, setos, plantas",
    "administrativo": "papeles: actas, juntas, certificados, recibos",
}


def catalogo_descrito(catalogo: list[str]) -> str:
    """'fontaneria (agua, tuberías…), electricidad (…), …' para los prompts."""
    return ", ".join(
        f"{c} ({DESCRIPCION_CATEGORIA[c]})" if c in DESCRIPCION_CATEGORIA else c for c in catalogo
    )


def sistema_clasificacion(catalogo: list[str]) -> str:
    return (
        "Eres el clasificador de incidencias de una comunidad de propietarios en España. "
        "Clasifica la solicitud del vecino en exactamente una categoría del catálogo cerrado: "
        f"{catalogo_descrito(catalogo)}. Si no encaja claramente en ninguna, usa "
        f"'{CATEGORIA_NO_CLASIFICABLE}'. La categoría la decide el elemento que falla, nunca "
        "el sitio donde está ni lo que tiene al lado: el enchufe junto al ascensor es "
        "electricidad y la grieta en la pared de los buzones es albanileria. Si no nombra el "
        "elemento que falla, no lo deduzcas del verbo aunque suela ir con uno ('se ha parado', "
        f"'no va', 'hace ruido' no dicen qué): usa '{CATEGORIA_NO_CLASIFICABLE}'. Si lo que falla "
        "no tiene categoría propia en el catálogo (gas, incendio, plagas…), usa "
        f"'{CATEGORIA_NO_CLASIFICABLE}', nunca la más parecida. "
        "Urgencia: baja (molestia sin riesgo ni daño), media "
        "(afecta al uso normal), alta (daño en curso, servicio esencial interrumpido o riesgo "
        "material), emergencia (riesgo inmediato para personas). Escribe un resumen de una frase "
        "corta en español, en tercera persona, con los datos que dice el vecino y sin inventar."
    )


def esquema_clasificacion(catalogo: list[str]) -> dict[str, Any]:
    return {
        "title": "clasificacion",
        "type": "object",
        "properties": {
            "categoria": {"type": "string", "enum": catalogo},
            "urgencia": {"type": "string", "enum": list(URGENCIAS)},
            "resumen": {"type": "string"},
        },
        "required": ["categoria", "urgencia", "resumen"],
    }
