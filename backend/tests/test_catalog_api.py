"""Edición del catálogo desde la consola: fincas, viviendas, profesionales, equipos y quién
atiende cada categoría. Lo que se edita aquí es exactamente lo que el asistente consulta."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import TEST_SECRET, make_session

from building_call.api.main import create_app
from building_call.domain.entities import Call, Incident
from building_call.graph import templates as tpl
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.settings import settings as global_settings

BASE = "/api/catalogs"


@pytest.fixture
async def client(deps: Dependencies, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(global_settings, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(global_settings, "session_secret", TEST_SECRET)
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        c.cookies.set("bc_session", make_session(deps.repo, "owner"))  # type: ignore[arg-type]
        c.repo = deps.repo  # type: ignore[attr-defined]
        yield c
    tpl.load({})


def _as_role(c: AsyncClient, role: str) -> AsyncClient:
    other = AsyncClient(transport=c._transport, base_url="http://localhost")
    other.cookies.set("bc_session", make_session(c.repo, role))  # type: ignore[attr-defined]
    return other


NEW_ESTATE = {
    "administrator_id": "adm-demo",
    "address": "Calle del Olmo 7",
    "email": "olmo7@demo.test",
    "official_phone": "+34910000077",
}


async def _create_estate(c: AsyncClient, **changes: str) -> dict:
    r = await c.post(f"{BASE}/estates", json={**NEW_ESTATE, **changes})
    assert r.status_code == 201, r.text
    return r.json()


def _actions(repo: MemoryRepository) -> list[str]:
    return [e["action"] for e in repo.audit]


async def _call_with_incident(
    repo: MemoryRepository,
    estate_id: str = "finca-mayor-12",
    home_id: str = "finca-mayor-12-u-1A",
    specialist_id: str | None = None,
    equipment_id: str | None = None,
) -> None:
    now = datetime.now(UTC)
    await repo.save_call(
        Call(id="ll-1", started_at=now, estate_id=estate_id, home_id=home_id)
    )
    await repo.save_incident(
        Incident(
            id="inc-1",
            call_id="ll-1",
            estate_id=estate_id,
            home_id=home_id,
            description="Fuga en el techo",
            category="plumbing",
            specialist_id=specialist_id,
            urgency="medium",
            created_at=now,
            equipment_id=equipment_id,
        )
    )


# ------------------------------------------------------------------------------------ permisos


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/estates"),
        ("put", "/estates/finca-mayor-12"),
        ("delete", "/estates/finca-mayor-12"),
        ("post", "/estates/finca-mayor-12/homes"),
        ("put", "/estates/finca-mayor-12/homes/finca-mayor-12-u-1A"),
        ("delete", "/estates/finca-mayor-12/homes/finca-mayor-12-u-1A"),
        ("post", "/estates/finca-mayor-12/equipment"),
        ("put", "/estates/finca-mayor-12/equipment/eq-mayor12-portero"),
        ("delete", "/estates/finca-mayor-12/equipment/eq-mayor12-portero"),
        ("post", "/specialists"),
        ("put", "/specialists/esp-fontanero"),
        ("delete", "/specialists/esp-fontanero"),
        ("put", "/administrators/adm-demo/categories/plumbing"),
    ],
)
async def test_quality_does_not_edit_the_catalog(client: AsyncClient, method: str, path: str) -> None:
    quality = _as_role(client, "quality")
    kwargs = {} if method == "delete" else {"json": {}}
    r = await getattr(quality, method)(BASE + path, **kwargs)
    assert r.status_code == 403
    await quality.aclose()


async def test_without_session_nothing_is_edited(client: AsyncClient) -> None:
    anonymous = AsyncClient(transport=client._transport, base_url="http://localhost")
    assert (await anonymous.post(f"{BASE}/estates", json=NEW_ESTATE)).status_code == 401
    await anonymous.aclose()


async def test_the_administrator_role_does_edit(client: AsyncClient) -> None:
    admin = _as_role(client, "administrator")
    r = await admin.post(f"{BASE}/estates", json=NEW_ESTATE)
    assert r.status_code == 201
    await admin.aclose()


# -------------------------------------------------------------------------------------- fincas


async def test_create_estate_generates_id_and_the_assistant_sees_it(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    estate = await _create_estate(client, id="finca-elegida-por-mi")  # type: ignore[arg-type]
    assert estate["id"] != "finca-elegida-por-mi" and estate["id"].startswith("estate-")
    assert estate["address"] == "Calle del Olmo 7"
    # El asistente la encuentra en su repositorio y avisa al contacto de la finca
    assert await repo.get_estate(estate["id"]) is not None
    cfg, cfg_id = await repo.effective_config(estate["id"])
    assert cfg.contact is not None and cfg.contact.email == "olmo7@demo.test"
    assert cfg_id
    assert "estate_created" in _actions(repo)


async def test_create_estate_validates_administrator_and_repeated_address(client: AsyncClient) -> None:
    r = await client.post(f"{BASE}/estates", json={**NEW_ESTATE, "administrator_id": "adm-x"})
    assert r.status_code == 422
    r = await client.post(f"{BASE}/estates", json={**NEW_ESTATE, "address": "calle mayor 12"})
    assert r.status_code == 409
    r = await client.post(f"{BASE}/estates", json={**NEW_ESTATE, "email": "no-es-correo"})
    assert r.status_code == 422


async def test_edit_estate_changes_data_and_creates_config_version(
    client: AsyncClient,
) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    _, before = await repo.effective_config("finca-alameda-5")
    r = await client.put(
        f"{BASE}/estates/finca-alameda-5",
        json={
            "address": "Avenida de la Alameda 5",
            "email": "nuevo@demo.test",
            "official_phone": "+34910000099",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "nuevo@demo.test"
    cfg, after = await repo.effective_config("finca-alameda-5")
    assert after != before
    assert cfg.contact is not None and cfg.contact.email == "nuevo@demo.test"
    # Lo demás de la capa de finca se conserva
    assert cfg.home_attempts == 4
    event = next(e for e in repo.audit if e["action"] == "estate_edited")
    assert event["detail"]["before"]["email"] == "alameda5@demo.test"
    assert (await client.put(f"{BASE}/estates/nada", json=NEW_ESTATE)).status_code == 404


async def test_delete_estate_without_activity(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    estate = await _create_estate(client)
    await client.post(f"{BASE}/estates/{estate['id']}/homes", json={"floor": "1", "door": "A"})
    r = await client.delete(f"{BASE}/estates/{estate['id']}")
    assert r.status_code == 200
    assert await repo.get_estate(estate["id"]) is None
    assert await repo.homes_of(estate["id"]) == []
    assert "estate_deleted" in _actions(repo)
    assert (await client.delete(f"{BASE}/estates/{estate['id']}")).status_code == 404


async def test_estate_with_calls_or_incidents_is_not_deleted(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    await _call_with_incident(repo)
    r = await client.delete(f"{BASE}/estates/finca-mayor-12")
    assert r.status_code == 409
    assert "llamada" in r.json()["detail"] and "incidencia" in r.json()["detail"]
    assert await repo.get_estate("finca-mayor-12") is not None


# ------------------------------------------------------------------------------------ viviendas


async def test_create_edit_and_delete_home(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    r = await client.post(
        f"{BASE}/estates/finca-mayor-12/homes",
        json={"id": "a-mano", "floor": "5", "door": "a"},
    )
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["id"] != "a-mano" and v["door"] == "A" and v["floor"] == "5"
    assert any(x.id == v["id"] for x in await repo.homes_of("finca-mayor-12"))

    # Repetida: el asistente no sabría cuál es
    r = await client.post(
        f"{BASE}/estates/finca-mayor-12/homes", json={"floor": "5", "door": "A"}
    )
    assert r.status_code == 409

    r = await client.put(
        f"{BASE}/estates/finca-mayor-12/homes/{v['id']}",
        json={"entrance": None, "floor": "5", "door": "C"},
    )
    assert r.status_code == 200 and r.json()["door"] == "C"

    r = await client.delete(f"{BASE}/estates/finca-mayor-12/homes/{v['id']}")
    assert r.status_code == 200
    assert all(x.id != v["id"] for x in await repo.homes_of("finca-mayor-12"))
    assert {"home_created", "home_edited", "home_deleted"} <= set(_actions(repo))


async def test_home_from_another_estate_gives_404(client: AsyncClient) -> None:
    r = await client.put(
        f"{BASE}/estates/finca-alameda-5/homes/finca-mayor-12-u-1A",
        json={"floor": "1", "door": "A"},
    )
    assert r.status_code == 404
    r = await client.post(f"{BASE}/estates/nada/homes", json={"floor": "1", "door": "A"})
    assert r.status_code == 404


async def test_home_with_incidents_is_not_deleted(client: AsyncClient) -> None:
    await _call_with_incident(client.repo)  # type: ignore[attr-defined]
    r = await client.delete(f"{BASE}/estates/finca-mayor-12/homes/finca-mayor-12-u-1A")
    assert r.status_code == 409


# ------------------------------------------------------------------------------------- equipos


async def test_create_edit_and_delete_equipment(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    r = await client.post(
        f"{BASE}/estates/finca-alameda-5/equipment",
        json={
            "kind": "grupo de presión",
            "location": "sótano",
            "brand": "Grundfos",
            "maintainer_id": "esp-fontanero",
            "warranty_until": "2027-05-01",
        },
    )
    assert r.status_code == 201, r.text
    eq = r.json()
    assert eq["id"].startswith("eq-") and eq["description"] == "grupo de presión Grundfos (sótano)"
    assert any(e.id == eq["id"] for e in await repo.equipment_of("finca-alameda-5"))

    r = await client.put(
        f"{BASE}/estates/finca-alameda-5/equipment/{eq['id']}",
        json={"kind": "grupo de presión", "contract": "GP-1", "maintainer_id": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contract"] == "GP-1" and r.json()["maintainer_id"] is None

    r = await client.delete(f"{BASE}/estates/finca-alameda-5/equipment/{eq['id']}")
    assert r.status_code == 200
    assert all(e.id != eq["id"] for e in await repo.equipment_of("finca-alameda-5"))
    assert {"equipment_created", "equipment_edited", "equipment_deleted"} <= set(_actions(repo))


async def test_the_maintainer_must_belong_to_the_same_administrator(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    from building_call.domain.entities import Administrator, Specialist

    repo.administrators["adm-otro"] = Administrator(id="adm-otro", name="Otra Admin")
    repo.specialists["esp-ajeno"] = Specialist(
        id="esp-ajeno",
        administrator_id="adm-otro",
        name="Ajeno",
        main_category="plumbing",
        contact_channel="email",
        target="ajeno@demo.test",
    )
    for maintainer in ("esp-ajeno", "esp-inexistente"):
        r = await client.post(
            f"{BASE}/estates/finca-mayor-12/equipment",
            json={"kind": "ascensor", "maintainer_id": maintainer},
        )
        assert r.status_code == 422, maintainer


async def test_equipment_with_incidents_is_not_deleted(client: AsyncClient) -> None:
    await _call_with_incident(client.repo, equipment_id="eq-mayor12-portero")  # type: ignore[attr-defined]
    r = await client.delete(f"{BASE}/estates/finca-mayor-12/equipment/eq-mayor12-portero")
    assert r.status_code == 409


# ------------------------------------------------------------------------------- profesionales

NEW_SPEC = {
    "administrator_id": "adm-demo",
    "name": "Pinturas Sol",
    "main_category": "painting",
    "contact_channel": "email",
    "target": "pinturas@demo.test",
}


async def test_create_and_edit_professional(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    r = await client.post(f"{BASE}/specialists", json={**NEW_SPEC, "id": "mio"})
    assert r.status_code == 201, r.text
    spec = r.json()
    assert spec["id"] != "mio" and spec["id"].startswith("spec-")
    assert any(e.id == spec["id"] for e in await repo.specialists_of("adm-demo"))

    r = await client.put(
        f"{BASE}/specialists/{spec['id']}",
        json={**NEW_SPEC, "contact_channel": "call", "target": "+34600111222"},
    )
    assert r.status_code == 200 and r.json()["contact_channel"] == "call"
    assert {"specialist_created", "specialist_edited"} <= set(_actions(repo))


async def test_professional_validates_category_and_target(client: AsyncClient) -> None:
    cases = [
        {"main_category": "astrologia"},
        {"contact_channel": "email", "target": "600111222"},
        {"contact_channel": "call", "target": "pinturas@demo.test"},
        {"contact_channel": "fax"},
        {"administrator_id": "adm-x"},
    ]
    for change in cases:
        r = await client.post(f"{BASE}/specialists", json={**NEW_SPEC, **change})
        assert r.status_code == 422, change


async def test_delete_unassigned_professional(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    spec = (await client.post(f"{BASE}/specialists", json=NEW_SPEC)).json()
    r = await client.delete(f"{BASE}/specialists/{spec['id']}")
    assert r.status_code == 200
    assert all(e.id != spec["id"] for e in await repo.specialists_of("adm-demo"))
    assert "specialist_deleted" in _actions(repo)


async def test_an_assigned_professional_is_not_silently_deleted(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    # Asignado a una categoría
    r = await client.delete(f"{BASE}/specialists/esp-fontanero")
    assert r.status_code == 409
    assert "Fontanería" in r.json()["detail"] or "fontaneria" in r.json()["detail"]
    # Mantiene un equipo
    r = await client.delete(f"{BASE}/specialists/esp-porteros")
    assert r.status_code == 409 and "portero" in r.json()["detail"]
    assert any(e.id == "esp-porteros" for e in await repo.specialists_of("adm-demo"))


async def test_force_unassigns_and_says_so(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    r = await client.delete(f"{BASE}/specialists/esp-ascensores", params={"force": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unassigned_categories"] == ["elevator"]
    assert body["equipment_without_maintainer"] == ["eq-mayor12-ascensor"]
    cfg, _ = await repo.effective_config("finca-mayor-12")
    assert "elevator" not in cfg.specialist_by_category
    ascensor = next(e for e in await repo.equipment_of("finca-mayor-12") if e.kind == "ascensor")
    assert ascensor.maintainer_id is None


async def test_professional_with_incidents_is_not_deleted(client: AsyncClient) -> None:
    await _call_with_incident(client.repo, specialist_id="esp-cerrajero")  # type: ignore[attr-defined]
    r = await client.delete(f"{BASE}/specialists/esp-cerrajero", params={"force": "true"})
    assert r.status_code == 409


# ------------------------------------------------------------------- quién atiende cada categoría


async def test_assign_specialist_creates_new_version(client: AsyncClient) -> None:
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    spec = (await client.post(f"{BASE}/specialists", json=NEW_SPEC)).json()
    before = repo.administrator_config["adm-demo"]
    versions = len(repo.config_history)

    r = await client.put(
        f"{BASE}/administrators/adm-demo/categories/painting", json={"specialist_id": spec["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["specialist_by_category"]["painting"] == spec["id"]
    # Versión nueva, la anterior intacta y en el historial
    assert len(repo.config_history) == versions + 1
    assert before.specialist_by_category and "painting" not in before.specialist_by_category
    assert repo.administrator_config["adm-demo"].ask_detail is False  # el resto se hereda
    cfg, _ = await repo.effective_config("finca-mayor-12")
    assert cfg.specialist_by_category["painting"] == spec["id"]
    event = next(e for e in repo.audit if e["action"] == "category_assigned")
    assert event["detail"] == {
        "category": "painting",
        "before": None,
        "after": spec["id"],
        "config_id": r.json()["config_id"],
    }

    # Quitar la asignación: queda para el administrador
    r = await client.put(
        f"{BASE}/administrators/adm-demo/categories/painting", json={"specialist_id": None}
    )
    assert r.status_code == 200 and "painting" not in r.json()["specialist_by_category"]


async def test_assign_validates_category_and_professional(client: AsyncClient) -> None:
    path = f"{BASE}/administrators/adm-demo/categories"
    r = await client.put(f"{path}/astrologia", json={"specialist_id": "esp-fontanero"})
    assert r.status_code == 422
    r = await client.put(f"{path}/painting", json={"specialist_id": "esp-inexistente"})
    assert r.status_code == 422
    r = await client.put(
        f"{BASE}/administrators/adm-x/categories/painting", json={"specialist_id": None}
    )
    assert r.status_code == 404


async def test_administrators_include_their_assignment(client: AsyncClient) -> None:
    admins = (await client.get(f"{BASE}/administrators")).json()
    demo = next(a for a in admins if a["id"] == "adm-demo")
    assert demo["specialist_by_category"]["plumbing"] == "esp-fontanero"
    assert "painting" in demo["category_catalog"]


async def test_assign_does_not_break_an_estate_with_its_own_catalog(client: AsyncClient) -> None:
    """Una finca que acota su catálogo heredaría una categoría que no conoce: el asistente
    no podría resolver su configuración. Se rechaza antes de guardar."""
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    version, layer = repo.estate_config["finca-alameda-5"]
    restricted = ["plumbing", "electrical", "elevator", "locksmith"]
    repo.estate_config["finca-alameda-5"] = (
        version,
        layer.model_copy(update={"category_catalog": restricted}),
    )
    spec = (await client.post(f"{BASE}/specialists", json=NEW_SPEC)).json()
    before = repo.administrator_config["adm-demo"]

    r = await client.put(
        f"{BASE}/administrators/adm-demo/categories/painting", json={"specialist_id": spec["id"]}
    )
    assert r.status_code == 409, r.text
    assert "Avenida de la Alameda 5" in r.json()["detail"]
    assert repo.administrator_config["adm-demo"] is before
    assert (await client.get(f"{BASE}/estates/finca-alameda-5")).status_code == 200


async def test_the_estate_says_if_it_sets_its_own_professionals(client: AsyncClient) -> None:
    """Si la capa de la finca fija quién atiende cada categoría, lo del administrador no le
    llega: la consola lo enseña en lugar de ofrecer un selector que no haría nada."""
    repo: MemoryRepository = client.repo  # type: ignore[attr-defined]
    version, layer = repo.estate_config["finca-alameda-5"]
    repo.estate_config["finca-alameda-5"] = (
        version,
        layer.model_copy(update={"specialist_by_category": {"plumbing": "esp-fontanero"}}),
    )
    own_one = (await client.get(f"{BASE}/estates/finca-alameda-5")).json()
    inherited = (await client.get(f"{BASE}/estates/finca-mayor-12")).json()
    assert own_one["own_assignment"] is True
    assert inherited["own_assignment"] is False
