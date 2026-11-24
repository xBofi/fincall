"""API del flujo: grafo, plantillas editables, prompts de solo lectura y catálogos."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import TEST_SECRET, make_session

from building_call.api.main import create_app
from building_call.graph import templates as tpl
from building_call.graph.manifest import MANIFEST
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.settings import settings as global_settings


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        c.repo = deps.repo  # type: ignore[attr-defined]
        yield c
    tpl.load({})


async def _owner(c: AsyncClient) -> None:
    c.cookies.set("bc_session", make_session(c.repo, "owner"))  # type: ignore[attr-defined]


async def _as_role(c: AsyncClient, role: str) -> AsyncClient:
    """Devuelve un cliente ya dentro con ese rol."""
    other = AsyncClient(transport=c._transport, base_url="http://localhost")
    other.cookies.set("bc_session", make_session(c.repo, role))  # type: ignore[attr-defined]
    return other


# ------------------------------------------------------------------------------------ el grafo


async def test_the_graph_describes_all_nodes_and_their_links(client: AsyncClient) -> None:
    await _owner(client)
    g = (await client.get("/api/graph")).json()
    assert {n["id"] for n in g["nodes"]} == set(MANIFEST)
    assert g["utterance"] == "next_step"
    # Toda arista sale y llega a algo conocido, y explica por qué
    ids = set(MANIFEST) | {"end"}
    for a in g["edges"]:
        assert a["origin"] in ids and a["target"] in ids
        assert a["condition"]
        assert a["times"] >= 0


async def test_the_graph_says_which_nodes_skip_the_model(client: AsyncClient) -> None:
    """Sin esto, alguien editaría un texto esperando cambiar cómo se entiende un 'sí'."""
    await _owner(client)
    nodes = {n["id"]: n for n in (await client.get("/api/graph")).json()["nodes"]}
    assert nodes["next_step"]["uses_llm"] is False
    assert nodes["identify_estate"]["uses_llm"] is True
    assert any("sí / no" in c for c in nodes["identify_estate"]["deterministic_paths"])
    assert nodes["create_ticket"]["effects"]


async def test_the_graph_declares_no_violations_after_the_suite(client: AsyncClient) -> None:
    await _owner(client)
    assert (await client.get("/api/graph")).json()["violations"] == []


# ------------------------------------------------------------------------------- los prompts


async def test_prompts_are_visible_but_not_editable(client: AsyncClient) -> None:
    await _owner(client)
    r = (await client.get("/api/prompts")).json()
    assert r["editables"] is False
    assert {p["id"] for p in r["prompts"]} == {"interpretation", "classification", "phrasing"}
    for p in r["prompts"]:
        assert p["text"] and p["version"] and p["contract"]
    # No existe forma de publicarlos
    assert (await client.put("/api/prompts/interpretation", json={"text": "x"})).status_code in (
        404,
        405,
    )


# ------------------------------------------------------------------------------- plantillas


async def test_list_templates_says_which_are_modified(client: AsyncClient) -> None:
    await _owner(client)
    rows = (await client.get("/api/templates")).json()
    assert {f["id"] for f in rows} == set(tpl.CATALOG)
    assert all(f["overridden"] is False and f["version"] == 0 for f in rows)


async def test_publishing_changes_what_the_bot_says_and_is_audited(
    client: AsyncClient, repo: MemoryRepository
) -> None:
    await _owner(client)
    r = await client.put(
        "/api/templates/identify_estate.candidate",
        json={"text": "¿Me confirma que su finca es {address}?"},
    )
    assert r.status_code == 200
    assert r.json()["overridden"] is True and r.json()["version"] == 1
    assert (
        tpl.render("identify_estate.candidate", address="Calle Mayor 12")
        == "¿Me confirma que su finca es Calle Mayor 12?"
    )
    audit = await repo.list_audit()
    assert any(e["action"] == "template_published" for e in audit)


async def test_publishing_something_invalid_returns_the_problems(client: AsyncClient) -> None:
    await _owner(client)
    r = await client.put(
        "/api/templates/identify_estate.candidate", json={"text": "He identificado la finca."}
    )
    assert r.status_code == 422
    problems = r.json()["detail"]["problems"]
    assert any("Faltan variables" in p for p in problems)
    # Y no ha llegado a cambiar nada
    assert not tpl.is_overridden("identify_estate.candidate")


async def test_restore_goes_back_to_the_code_text(client: AsyncClient) -> None:
    await _owner(client)
    await client.put("/api/templates/global.filler", json={"text": "Sin prisa, le espero."})
    assert tpl.is_overridden("global.filler")
    r = await client.delete("/api/templates/global.filler")
    assert r.status_code == 200 and r.json()["overridden"] is False
    assert tpl.effective_text("global.filler") == tpl.CATALOG["global.filler"].text


async def test_the_history_keeps_what_was_published(client: AsyncClient) -> None:
    await _owner(client)
    await client.put("/api/templates/global.filler", json={"text": "Primera versión."})
    await client.put("/api/templates/global.filler", json={"text": "Segunda versión."})
    detail = (await client.get("/api/templates/global.filler")).json()
    assert [h["version"] for h in detail["history"]] == [2, 1]
    assert detail["original_text"] == tpl.CATALOG["global.filler"].text


async def test_validate_previews_without_publishing(client: AsyncClient) -> None:
    await _owner(client)
    r = await client.post(
        "/api/templates/identify_estate.candidate/validate",
        json={"text": "¿Me confirma {address}?"},
    )
    assert r.status_code == 200
    assert r.json()["problems"] == []
    assert not tpl.is_overridden("identify_estate.candidate")


async def test_a_nonexistent_template_gives_404(client: AsyncClient) -> None:
    await _owner(client)
    assert (await client.get("/api/templates/inventada.total")).status_code == 404
    assert (
        await client.put("/api/templates/inventada.total", json={"text": "hola"})
    ).status_code == 404


# ------------------------------------------------------------------------------- permisos


async def test_only_the_owner_edits_templates(client: AsyncClient) -> None:
    await _owner(client)
    quality = await _as_role(client, "quality")
    # Ver, sí
    assert (await quality.get("/api/templates")).status_code == 200
    assert (await quality.get("/api/graph")).status_code == 200
    # Tocar, no
    assert (
        await quality.put("/api/templates/global.filler", json={"text": "Otra cosa."})
    ).status_code == 403
    assert (await quality.delete("/api/templates/global.filler")).status_code == 403
    assert not tpl.is_overridden("global.filler")
    await quality.aclose()


async def test_without_session_nothing_is_visible(client: AsyncClient) -> None:
    for path in ("/api/graph", "/api/prompts", "/api/templates", "/api/catalogs/estates"):
        assert (await client.get(path)).status_code == 401, path


# ------------------------------------------------------------------------------- catálogos


async def test_catalogs_show_estates_and_professionals(client: AsyncClient) -> None:
    await _owner(client)
    estates = (await client.get("/api/catalogs/estates")).json()
    assert estates and all(f["address"] and f["administrator"] for f in estates)

    detail = (await client.get(f"/api/catalogs/estates/{estates[0]['id']}")).json()
    assert detail["homes"]
    # Las tres capas ya resueltas, para entender por qué esta finca se comporta así
    assert detail["effective_config"]["category_catalog"]

    admins = (await client.get("/api/catalogs/administrators")).json()
    assert admins and admins[0]["estates"]

    specs = (await client.get("/api/catalogs/specialists")).json()
    assert all(e["main_category"] and e["administrator"] for e in specs)


async def test_a_nonexistent_estate_gives_404(client: AsyncClient) -> None:
    await _owner(client)
    assert (await client.get("/api/catalogs/estates/finca-fantasma")).status_code == 404


# ------------------------------------------------------------------------------- auditoría


async def test_the_trace_says_which_template_and_version_each_phrase_came_from(
    client: AsyncClient,
) -> None:
    """Con textos editables, guardar solo la frase no permite saber qué se dijo ni con qué versión."""
    await _owner(client)
    call = (await client.post("/api/calls", json={})).json()
    traces = [t for t in call["trace"] if t["action"] == "template"]
    assert traces, "el saludo inicial no dejó constancia de su plantilla"
    assert traces[0]["detail"] == "identify_estate.greeting v0"

    # Tras editar, la versión que queda registrada es la nueva
    await client.put(
        "/api/templates/identify_estate.greeting",
        json={"text": "Buenas, soy el asistente de su comunidad. ¿En qué finca está?"},
    )
    other = (await client.post("/api/calls", json={})).json()
    traces = [t for t in other["trace"] if t["action"] == "template"]
    assert traces[0]["detail"] == "identify_estate.greeting v1"
    assert "Buenas, soy el asistente" in other["bot_messages"][0]


async def test_the_estate_card_lists_its_equipment(client: AsyncClient) -> None:
    await _owner(client)
    estate = (await client.get("/api/catalogs/estates/finca-mayor-12")).json()
    intercom = next(e for e in estate["equipment_list"] if e["id"] == "eq-mayor12-portero")
    assert intercom["maintainer_id"] == "esp-porteros" and intercom["contract"] == "PL-2024-118"
    assert intercom["description"] == "portero automático Fermax Citymax (portal)"
