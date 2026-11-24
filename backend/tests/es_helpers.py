"""Modelo falso de la suite en español: reglas simples sobre texto en español.

Módulo aparte (y no `conftest`) para que los tests lo importen por nombre sin chocar con el
`conftest` de la suite en inglés (`tests/en`).
"""

from __future__ import annotations

import re
from typing import Any

from building_call.llm import wire


def _work_type_and_equipment(u: str, system: str) -> dict[str, str]:
    """Vandalismo o robo -> siniestro; el equipo, si la frase nombra su tipo y está en la lista."""
    output: dict[str, str] = {}
    if any(p in u for p in ("vandaliz", "robado", "reventado")):
        output["work_type"] = "insurance_claim"
    for eq_id, description in re.findall(r"(eq-[\w-]+) \(([^)]*)\)", system):
        kind = description.split()[0]  # "portero", "ascensor", "puerta"
        if kind in u:
            output["equipment"] = eq_id
    return output


def _classify(u: str, catalog: list[str]) -> tuple[str, str]:
    if any(p in u for p in ("gas", "humo", "fuego")):
        cat, urg = "unclassifiable", "emergency"
    elif "agua" in u or "fuga" in u or "grifo" in u:
        cat, urg = "plumbing", "high" if "inund" in u else "medium"
    elif "luz" in u or "enchufe" in u:
        cat, urg = "electrical", "medium"
    elif "ascensor" in u:
        cat, urg = "elevator", "emergency" if "dentro" in u else "high"
    elif "limpieza" in u or "basura" in u:
        cat, urg = "cleaning", "low"
    else:
        cat, urg = "unclassifiable", "medium"
    assert cat in catalog
    return cat, urg


def _domain_catalog(schema: dict[str, Any]) -> list[str]:
    """El catálogo del esquema, que el modelo ve en español, con los códigos del dominio."""
    return [wire.from_model(wire.CATEGORIES, c) for c in schema["properties"]["categoria"]["enum"]]


class FakeLLM:
    """Simula la extracción estructurada con reglas simples. Despacha por `title` del esquema."""

    name = "falso/deterministico"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.system = ""

    async def available(self) -> bool:
        return True

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        title = schema.get("title")
        self.calls.append((title or "", user))
        self.system = system
        if title == "interpretation":
            return self._interpret(user, schema)
        if title == "phrasing":
            m = re.search(r'MENSAJE OBLIGATORIO: """(.*)"""', user, re.DOTALL)
            return {"reply": "Vale. " + (m.group(1) if m else "")}
        if title == "classification":
            catalog = _domain_catalog(schema)
            cat, urg = _classify(user.lower(), catalog)
            return {
                "category": cat,
                "urgency": urg,
                "summary": f"El vecino informa: {user}",
                "work_type": "repair",
                **_work_type_and_equipment(user.lower(), system),
            }
        raise AssertionError(f"esquema desconocido: {title}")

    def _interpret(self, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        # El mensaje de usuario trae la pregunta y la respuesta; interpretamos solo la respuesta
        m = re.search(r'Respuesta del vecino: """(.*)"""', user, re.DOTALL)
        text = (m.group(1) if m else user).strip()
        u = text.lower()
        output: dict[str, Any] = {}
        if any(p in u for p in ("hablar con una persona", "con alguien", "una persona")):
            return {"intent": "human"}
        if any(p in u for p in ("adiós", "adios", "ya llamaré", "hasta luego")):
            return {"intent": "hang_up"}
        if any(p in u for p in ("no le he oído", "repita", "¿cómo?", "no he entendido")):
            return {"intent": "repeat"}
        if "se está grabando" in u or "para qué necesita" in u:
            return {"intent": "question", "question": text}
        if "cuánto pago" in u or "cuota" in u:
            return {"intent": "out_of_scope"}
        if u.startswith(("sí", "si,", "si ", "eso es", "correcto")) or u in ("sí", "si"):
            output["confirms"] = True
        elif u.startswith("no"):
            output["confirms"] = False
        m_addr = re.search(
            r"(calle mayor \w+|avenida (?:de la )?alameda \w+|alameda \w+|mayor \w+)", u
        )
        if m_addr:
            output["address"] = m_addr.group(1)
        m_entrance = re.search(r"portal\s*(\w+)", u)
        remainder = re.sub(r"portal\s*\w+", " ", u)
        floors = re.findall(
            r"\b(\d+|primero|segundo|tercero|cuarto|bajo)\b\s*[ºo°]?\s*([a-d])\b", remainder
        )
        m_floor = floors[-1] if floors else None  # una corrección menciona el dato bueno al final
        if m_entrance:
            output["entrance"] = m_entrance.group(1)
        if m_floor:
            output["floor"], output["door"] = m_floor[0], m_floor[1].upper()
        m_name = re.search(r"(?:soy|me llamo)\s+([a-záéíóúñ]+(?:\s+(?!y\b)[a-záéíóúñ]+)?)", u)
        if m_name:
            output["name"] = " ".join(w.capitalize() for w in m_name.group(1).split())
        elif (
            re.fullmatch(r"[a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?", u)
            and "confirms" not in output
            and not m_addr
        ):
            output["name"] = text.title()
        problem_words = (
            "fuga",
            "agua",
            "luz",
            "ascensor",
            "basura",
            "ovni",
            "enchufe",
            "puerta del",
            "roto",
            "rota",
            "ruido",
            "parado",
            "portero",
            "gas",
            "humo",
            "fuego",
            "gotea",
            "grifo",
            "pintar",
        )
        if any(p in u for p in problem_words) and "problem" not in output:
            output["problem"] = text
            if "categoria" in schema["properties"]:
                cat, urg = _classify(u, _domain_catalog(schema))
                output.update(category=cat, urgency=urg, summary=f"El vecino informa: {text}")
                output.update(_work_type_and_equipment(u, self.system))
        if "pero eso no lo arregla" in u:
            output["objection"] = text.split("pero", 1)[1].strip()
        if "corregir" in u or "me he equivocado" in u or "antes le he dicho" in u:
            output["intent"] = "correct"
        return output
