"""Fixtures of the English suite: the same flows as `tests/`, with the English language entry,
the US demo buildings (12 Main Street, 5 Elm Avenue) and an English fake model."""

from __future__ import annotations

import pytest
from en_helpers import FakeLLM

from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager
from building_call.settings import settings


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calls default to English (`BC_LANGUAGE=en`), as on a US phone number."""
    monkeypatch.setattr(settings, "language", "en")


@pytest.fixture
def repo() -> MemoryRepository:
    # Flow tests don't ask the detail turn (configurable per property manager)
    r = MemoryRepository("en")
    r.administrator_config["adm-demo"] = r.administrator_config["adm-demo"].model_copy(
        update={"ask_detail": False}
    )
    return r


@pytest.fixture
def detail_manager(llm: FakeLLM) -> CallManager:
    return CallManager(Dependencies(repo=MemoryRepository("en"), llm=llm))


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()
