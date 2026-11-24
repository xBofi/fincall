from __future__ import annotations

import pytest
from es_helpers import FakeLLM
from session_helpers import TEST_SECRET

from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager
from building_call.settings import settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """La suite no depende del `.env` del desarrollador.

    Sin esto, un `BC_REPOSITORY=postgres` en la máquina de quien programa cambia el
    comportamiento de tests que nada tienen que ver con la base de datos, y la CI —que no tiene
    `.env`— ejecuta algo distinto de lo que se probó en local. Un test que quiera el modo real lo
    dice explícitamente.
    """
    monkeypatch.setattr(settings, "repository", "memory")
    # Spanish by default here; `tests/en` switches to English
    monkeypatch.setattr(settings, "language", "es")
    monkeypatch.setattr(settings, "session_secret", TEST_SECRET)


@pytest.fixture
def repo() -> MemoryRepository:
    # Los tests de flujo no piden el turno de detalle (configurable por administrador)
    r = MemoryRepository("es")
    r.administrator_config["adm-demo"] = r.administrator_config["adm-demo"].model_copy(
        update={"ask_detail": False}
    )
    return r


@pytest.fixture
def detail_manager(llm: FakeLLM) -> CallManager:
    return CallManager(Dependencies(repo=MemoryRepository("es"), llm=llm))


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def deps(repo: MemoryRepository, llm: FakeLLM) -> Dependencies:
    return Dependencies(repo=repo, llm=llm)


@pytest.fixture
def manager(deps: Dependencies) -> CallManager:
    return CallManager(deps)
