from __future__ import annotations

import re
from typing import Any

import pytest

from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas


class LLMFalso:
    """Simula la extracción estructurada con reglas simples. Despacha por `title` del esquema."""

    nombre = "falso/deterministico"

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str]] = []

    async def disponible(self) -> bool:
        return True

    async def extraer(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        titulo = esquema.get("title")
        self.llamadas.append((titulo or "", usuario))
        u = usuario.lower()
        if titulo == "vivienda":
            portal = re.search(r"portal\s*(\w+)", u)
            resto = re.sub(r"portal\s*\w+", " ", u)
            piso = re.search(r"(\d+|primero|segundo|tercero|cuarto)\s*[ºo°]?", resto)
            puerta = re.search(r"(?:puerta|letra)?\s*\b([a-d])\b", resto)
            return {
                "portal": portal.group(1) if portal else None,
                "piso": piso.group(1) if piso else None,
                "puerta": puerta.group(1) if puerta else None,
            }
        if titulo == "nombre":
            m = re.search(r"(?:soy|me llamo)\s+([\wáéíóú]+)", u)
            return {"nombre": m.group(1).capitalize() if m else None}
        if titulo == "clasificacion":
            catalogo = esquema["properties"]["categoria"]["enum"]
            if "agua" in u or "fuga" in u or "grifo" in u:
                cat, urg = "fontaneria", "alta" if "inund" in u else "media"
            elif "luz" in u or "enchufe" in u:
                cat, urg = "electricidad", "media"
            elif "ascensor" in u:
                cat, urg = "ascensor", "alta"
            elif "ovni" in u:
                cat, urg = "no_clasificable", "baja"
            elif "limpieza" in u or "basura" in u:
                cat, urg = "limpieza", "baja"
            else:
                cat, urg = "no_clasificable", "media"
            assert cat in catalogo
            return {"categoria": cat, "urgencia": urg, "resumen": f"El vecino informa: {usuario}"}
        if titulo == "confirmacion":
            return {"confirma": None}
        raise AssertionError(f"esquema desconocido: {titulo}")


@pytest.fixture
def repo() -> RepositorioMemoria:
    return RepositorioMemoria()


@pytest.fixture
def llm() -> LLMFalso:
    return LLMFalso()


@pytest.fixture
def deps(repo: RepositorioMemoria, llm: LLMFalso) -> Dependencias:
    return Dependencias(repo=repo, llm=llm)


@pytest.fixture
def gestor(deps: Dependencias) -> GestorLlamadas:
    return GestorLlamadas(deps)
