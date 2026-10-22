from __future__ import annotations

import re
from typing import Any

import pytest

from building_call.graph.nodes import Dependencies
from building_call.llm import wire
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager
from building_call.settings import settings

# Con largo real: por debajo del mínimo que exige la API es como no tener secreto
TEST_SECRET = "secreto-de-prueba-con-largo-suficiente-0123456789"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """La suite no depende del `.env` del desarrollador.

    Sin esto, un `BC_REPOSITORY=postgres` en la máquina de quien programa cambia el
    comportamiento de tests que nada tienen que ver con la base de datos, y la CI —que no tiene
    `.env`— ejecuta algo distinto de lo que se probó en local. Un test que quiera el modo real lo
    dice explícitamente.
    """
    monkeypatch.setattr(settings, "repository", "memory")
    monkeypatch.setattr(settings, "session_secret", TEST_SECRET)


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


@pytest.fixture
def repo() -> MemoryRepository:
    # Los tests de flujo no piden el turno de detalle (configurable por administrador)
    r = MemoryRepository()
    r.administrator_config["adm-demo"] = r.administrator_config["adm-demo"].model_copy(
        update={"ask_detail": False}
    )
    return r


@pytest.fixture
def detail_manager(llm: FakeLLM) -> CallManager:
    return CallManager(Dependencies(repo=MemoryRepository(), llm=llm))


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def deps(repo: MemoryRepository, llm: FakeLLM) -> Dependencies:
    return Dependencies(repo=repo, llm=llm)


def make_session(repo: MemoryRepository, role: str = "owner") -> str:
    """Inserta un usuario y una sesión vigente en el repositorio en memoria; devuelve el token
    para la cookie `bc_session`. Síncrono a propósito: sirve también con `TestClient`."""
    from datetime import UTC, datetime, timedelta

    from building_call.auth import hash_session_token
    from building_call.domain.operations import User
    from building_call.repos.operational import AccessSession

    repo._operational()
    user = User(
        id=f"usr-{role}",
        username=str(role),
        role=role,
        active=True,
        password_hash="x",  # type: ignore[arg-type]
    )
    repo.users[user.id] = user
    token = f"token-de-prueba-{role}"
    now = datetime.now(UTC)
    repo.sessions[hash_session_token(token)] = AccessSession(
        f"ses-{role}", user.id, hash_session_token(token), now, now + timedelta(hours=1)
    )
    return token


@pytest.fixture
def manager(deps: Dependencies) -> CallManager:
    return CallManager(deps)
