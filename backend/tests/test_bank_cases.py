"""Calidad marca turnos reales del vecino como casos del banco de pruebas.

Cada fallo real que se convierte en un caso del banco (`evals/gold_*.jsonl`) es lo que más ha
mejorado al asistente: la revisión de calidad lo hace desde el expediente de la llamada.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import TEST_SECRET, make_session

from building_call.api.main import create_app
from building_call.bank_cases import pseudonymize, strip_phones
from building_call.domain.operations import Role
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.settings import settings as global_settings


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _converse(client: AsyncClient, repo: MemoryRepository, turns: list[str]) -> str:
    client.cookies.set("bc_session", make_session(repo))
    cid = (await client.post("/api/calls", json={"caller_phone": "+34600111222"})).json()[
        "call_id"
    ]
    for t in turns:
        await client.post(f"/api/calls/{cid}/messages", json={"text": t})
    return cid


def _as_role(client: AsyncClient, repo: MemoryRepository, role: Role) -> None:
    client.cookies.set("bc_session", make_session(repo, str(role)))


CASE = {
    "turn": 1,
    "expected": {"intent": "continue", "address": "mayor 12"},
    "note": "No pilló la calle",
}


@pytest.mark.parametrize("role", [Role.quality, Role.administrator, Role.owner])
async def test_reviewers_mark_a_turn_as_a_case(
    client: AsyncClient, repo: MemoryRepository, role: Role
) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12", "sí"])
    _as_role(client, repo, role)

    r = await client.post(f"/api/operations/calls/{cid}/cases", json=CASE)
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["turn"] == 1 and case["bank"] == "interpretation"
    assert case["utterance"] == "Calle Mayor 12"
    assert case["node"] == "identify_estate"
    # La pregunta es lo último que dijo el bot antes de que el vecino hablara
    detail = (await client.get(f"/api/operations/calls/{cid}")).json()
    assert case["question"] == detail["turns"][0]["assistant"][-1]
    assert case["author_id"] == f"usr-{role}" and case["created_at"]
    assert case["expected"] == CASE["expected"] and case["note"] == "No pilló la calle"
    # Nunca guarda el teléfono del vecino
    assert "600111222" not in str(case)

    # Aparece en el expediente y en el listado general
    assert [c["id"] for c in detail["cases"]] == [case["id"]]
    todos = (await client.get("/api/operations/cases")).json()
    assert [c["id"] for c in todos] == [case["id"]]


async def test_without_session_nothing_is_marked(client: AsyncClient, repo: MemoryRepository) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12"])
    client.cookies.clear()
    assert (
        await client.post(f"/api/operations/calls/{cid}/cases", json=CASE)
    ).status_code == 401
    assert (await client.get("/api/operations/cases")).status_code == 401


async def test_mark_is_audited_and_unmark_too(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12"])
    _as_role(client, repo, Role.quality)
    case = (await client.post(f"/api/operations/calls/{cid}/cases", json=CASE)).json()
    _as_role(client, repo, Role.owner)
    events = await repo.list_audit()
    marked = next(e for e in events if e["action"] == "mark_test_bank_case")
    assert marked["user_id"] == "usr-quality"
    assert marked["resource"] == f"call:{cid}"
    assert marked["detail"]["turn"] == 1 and marked["detail"]["case_id"] == case["id"]

    assert (await client.delete(f"/api/operations/cases/{case['id']}")).status_code == 204
    assert (await client.get("/api/operations/cases")).json() == []
    events = await repo.list_audit()
    assert any(e["action"] == "unmark_test_bank_case" for e in events)
    assert (await client.delete(f"/api/operations/cases/{case['id']}")).status_code == 404


async def test_marking_the_same_turn_again_corrects_it(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12"])
    _as_role(client, repo, Role.quality)
    primero = (await client.post(f"/api/operations/calls/{cid}/cases", json=CASE)).json()
    other = {**CASE, "expected": {"address": "mayor 12"}, "note": "mejor así"}
    segundo = (await client.post(f"/api/operations/calls/{cid}/cases", json=other)).json()
    assert segundo["id"] == primero["id"]
    todos = (await client.get("/api/operations/cases")).json()
    assert len(todos) == 1 and todos[0]["expected"] == {"address": "mayor 12"}


@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({**CASE, "turn": 0}, 422),  # el saludo no tiene texto del vecino
        ({**CASE, "turn": 99}, 404),
        ({**CASE, "expected": {}}, 422),
        ({**CASE, "expected": {"intent": "bailar"}}, 422),
        ({**CASE, "expected": {"urgency": "altisima"}}, 422),
        ({**CASE, "expected": {"work_type": ["repair", "magia"]}}, 422),
        ({**CASE, "expected": {"confirms": "quizá"}}, 422),
        ({**CASE, "expected": {"phone": "600"}}, 422),
        ({**CASE, "bank": "classification", "expected": {"category": "plumbing"}}, 422),
        # Como en el banco: "*" vale cualquiera también para confirma, y null es sin dato
        ({**CASE, "expected": {"confirms": "*"}}, 201),
        ({**CASE, "expected": {"confirms": [True, None]}}, 201),
    ],
)
async def test_validates_what_is_marked(
    client: AsyncClient, repo: MemoryRepository, body: dict, state: int
) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12"])
    _as_role(client, repo, Role.quality)
    r = await client.post(f"/api/operations/calls/{cid}/cases", json=body)
    assert r.status_code == state, r.text


async def test_nonexistent_call(client: AsyncClient, repo: MemoryRepository) -> None:
    _as_role(client, repo, Role.quality)
    r = await client.post("/api/operations/calls/llam-nada/cases", json=CASE)
    assert r.status_code == 404


async def test_classification_case_and_wildcards(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12", "sí", "3 B", "Gotea el grifo"])
    _as_role(client, repo, Role.quality)
    r = await client.post(
        f"/api/operations/calls/{cid}/cases",
        json={
            "turn": 4,
            "bank": "classification",
            "expected": {"category": "plumbing", "urgency": ["low", "medium"]},
        },
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        f"/api/operations/calls/{cid}/cases",
        json={
            "turn": 4,
            "expected": {"problem": "*", "category": "plumbing", "equipment": None},
            "heard_text": "Cuénteme",
        },
    )
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["catalog"] is True and case["heard_text"] == "Cuénteme"
    assert len((await client.get(f"/api/operations/calls/{cid}/cases")).json()) == 2


async def test_the_caller_name_is_pseudonymised(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    cid = await _converse(client, repo, ["Calle Mayor 12", "sí", "3 B", "Soy Remedios Quintana"])
    _as_role(client, repo, Role.quality)
    case = (
        await client.post(
            f"/api/operations/calls/{cid}/cases",
            json={"turn": 4, "expected": {"name": "Remedios Quintana"}},
        )
    ).json()
    assert "Remedios" not in str(case) and "Quintana" not in str(case)
    assert case["utterance"] == "Soy Carmen Ruiz"
    assert case["expected"] == {"name": "Carmen Ruiz"}


def test_pseudonymise_and_strip_phones() -> None:
    assert pseudonymize("hola, soy remedios", "Remedios") == "hola, soy Carmen"
    assert pseudonymize("Soy Ana", None) == "Soy Ana"
    assert pseudonymize("Soy Ana", "Ana López") == "Soy Carmen"
    assert strip_phones("llamadme al 600 11 22 33 o +34 912345678") == (
        "llamadme al 600 000 000 o 600 000 000"
    )
    assert strip_phones("tercero B del 12") == "tercero B del 12"
