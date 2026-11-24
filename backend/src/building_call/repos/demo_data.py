"""Demo data of the in-memory repository, one set per language.

The Spanish set is the original one (Calle Mayor 12, Avenida de la Alameda 5); the English one
comes from the US version of the agent (12 Main Street, 5 Elm Avenue). Both have the same shape
(two buildings, one with entrances, the same equipment and contractors), so the same flows can
be tested in either language. `BC_LANGUAGE` picks the set in memory mode and in `seed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from building_call.domain.config import Contact, PartialConfig
from building_call.domain.entities import Administrator, Equipment, Estate, Home, Specialist

if TYPE_CHECKING:
    from building_call.repos.memory import MemoryRepository


def _homes(
    estate_id: str, entrances: list[str | None], floors: int, doors: list[str]
) -> list[Home]:
    out: list[Home] = []
    for entrance in entrances:
        for floor in range(1, floors + 1):
            for door in doors:
                hid = f"{estate_id}-{entrance or 'u'}-{floor}{door}"
                out.append(
                    Home(
                        id=hid, estate_id=estate_id, entrance=entrance, floor=str(floor), door=door
                    )
                )
    return out


def spanish(repo: MemoryRepository) -> None:
    """Comunidades de propietarios en España."""
    repo.administrators = {
        "adm-demo": Administrator(id="adm-demo", name="Administraciones Demo SL"),
    }
    repo.specialists = {
        "esp-fontanero": Specialist(
            id="esp-fontanero",
            administrator_id="adm-demo",
            name="Fontanería Pérez",
            main_category="plumbing",
            contact_channel="email",
            target="fontaneria@demo.test",
        ),
        "esp-electricista": Specialist(
            id="esp-electricista",
            administrator_id="adm-demo",
            name="Electricidad Gómez",
            main_category="electrical",
            contact_channel="email",
            target="electricidad@demo.test",
        ),
        "esp-ascensores": Specialist(
            id="esp-ascensores",
            administrator_id="adm-demo",
            name="Ascensores Norte",
            main_category="elevator",
            contact_channel="call",
            target="+34600000001",
        ),
        "esp-cerrajero": Specialist(
            id="esp-cerrajero",
            administrator_id="adm-demo",
            name="Cerrajería Rápida",
            main_category="locksmith",
            contact_channel="email",
            target="cerrajeria@demo.test",
        ),
        # Mantenedores de equipos concretos (no son el especialista de ninguna categoría)
        "esp-porteros": Specialist(
            id="esp-porteros",
            administrator_id="adm-demo",
            name="Porteros Levante",
            main_category="electrical",
            contact_channel="email",
            target="soporte@porteroslevante.demo.test",
        ),
        "esp-automatismos": Specialist(
            id="esp-automatismos",
            administrator_id="adm-demo",
            name="Automatismos Sur",
            main_category="locksmith",
            contact_channel="call",
            target="+34600000002",
        ),
    }
    # Inventario de demostración, hasta que el administrador dé el real (Pendiente)
    repo.equipment_list = {
        "finca-mayor-12": [
            Equipment(
                id="eq-mayor12-portero",
                estate_id="finca-mayor-12",
                kind="portero automático",
                location="portal",
                brand="Fermax",
                model="Citymax",
                installer="Porteros Levante",
                maintainer_id="esp-porteros",
                contract="PL-2024-118",
            ),
            Equipment(
                id="eq-mayor12-ascensor",
                estate_id="finca-mayor-12",
                kind="ascensor",
                brand="Orona",
                installer="Orona",
                maintainer_id="esp-ascensores",
                contract="AN-5531",
            ),
        ],
        "finca-alameda-5": [
            Equipment(
                id="eq-alameda5-garaje",
                estate_id="finca-alameda-5",
                kind="puerta de garaje automática",
                location="garaje",
                brand="Nice",
                installer="Automatismos Sur",
                maintainer_id="esp-automatismos",
            ),
        ],
    }
    repo.estates = {
        "finca-mayor-12": Estate(
            id="finca-mayor-12",
            administrator_id="adm-demo",
            address="Calle Mayor 12",
            email="mayor12@demo.test",
            official_phone="+34910000001",
        ),
        "finca-alameda-5": Estate(
            id="finca-alameda-5",
            administrator_id="adm-demo",
            address="Avenida de la Alameda 5",
            email="alameda5@demo.test",
            official_phone="+34910000002",
        ),
    }
    repo.homes = {
        "finca-mayor-12": _homes("finca-mayor-12", [None], 4, ["A", "B"]),
        "finca-alameda-5": _homes("finca-alameda-5", ["1", "2"], 3, ["A", "B", "C"]),
    }
    repo.administrator_config = {
        "adm-demo": PartialConfig(
            specialist_by_category={
                "plumbing": "esp-fontanero",
                "electrical": "esp-electricista",
                "elevator": "esp-ascensores",
                "locksmith": "esp-cerrajero",
            },
        ),
    }
    # Id de la versión vigente de cada capa de administrador (como en `configs`)
    repo.administrator_config_id = {"adm-demo": "cfg-adm-demo-v1"}
    # Versión de configuración por finca: (id_version, documento)
    repo.estate_config = {
        "finca-mayor-12": (
            "cfg-mayor-12-v1",
            PartialConfig(
                contact=Contact(email="mayor12@demo.test", official_phone="+34910000001"),
            ),
        ),
        "finca-alameda-5": (
            "cfg-alameda-5-v1",
            PartialConfig(
                contact=Contact(email="alameda5@demo.test", official_phone="+34910000002"),
                home_attempts=4,
            ),
        ),
    }


def english(repo: MemoryRepository) -> None:
    """Residential buildings in the US."""
    repo.administrators = {
        "adm-demo": Administrator(id="adm-demo", name="Demo Property Management LLC"),
    }
    repo.specialists = {
        "spc-plumber": Specialist(
            id="spc-plumber",
            administrator_id="adm-demo",
            name="Smith Plumbing",
            main_category="plumbing",
            contact_channel="email",
            target="plumbing@demo.test",
        ),
        "spc-electrician": Specialist(
            id="spc-electrician",
            administrator_id="adm-demo",
            name="Bright Electric",
            main_category="electrical",
            contact_channel="email",
            target="electric@demo.test",
        ),
        "spc-elevators": Specialist(
            id="spc-elevators",
            administrator_id="adm-demo",
            name="North Elevators",
            main_category="elevator",
            contact_channel="call",
            target="+15550200001",
        ),
        "spc-locksmith": Specialist(
            id="spc-locksmith",
            administrator_id="adm-demo",
            name="QuickLock Locksmiths",
            main_category="locksmith",
            contact_channel="email",
            target="locksmith@demo.test",
        ),
        # Mantenedores de equipos concretos (no son el especialista de ninguna categoría)
        "spc-intercoms": Specialist(
            id="spc-intercoms",
            administrator_id="adm-demo",
            name="Eastside Intercoms",
            main_category="electrical",
            contact_channel="email",
            target="support@eastsideintercoms.demo.test",
        ),
        "spc-gates": Specialist(
            id="spc-gates",
            administrator_id="adm-demo",
            name="South Gate Automation",
            main_category="locksmith",
            contact_channel="call",
            target="+15550200002",
        ),
    }
    # Inventario de demostración, hasta que el administrador dé el real (Pendiente)
    repo.equipment_list = {
        "estate-main-12": [
            Equipment(
                id="eq-main12-intercom",
                estate_id="estate-main-12",
                kind="intercom",
                location="main entrance",
                brand="Fermax",
                model="Citymax",
                installer="Eastside Intercoms",
                maintainer_id="spc-intercoms",
                contract="EI-2024-118",
            ),
            Equipment(
                id="eq-main12-elevator",
                estate_id="estate-main-12",
                kind="elevator",
                brand="Otis",
                installer="Otis",
                maintainer_id="spc-elevators",
                contract="NE-5531",
            ),
        ],
        "estate-elm-5": [
            Equipment(
                id="eq-elm5-garage",
                estate_id="estate-elm-5",
                kind="garage door",
                location="garage",
                brand="Nice",
                installer="South Gate Automation",
                maintainer_id="spc-gates",
            ),
        ],
    }
    repo.estates = {
        "estate-main-12": Estate(
            id="estate-main-12",
            administrator_id="adm-demo",
            address="12 Main Street",
            email="main12@demo.test",
            official_phone="+15550100001",
        ),
        "estate-elm-5": Estate(
            id="estate-elm-5",
            administrator_id="adm-demo",
            address="5 Elm Avenue",
            email="elm5@demo.test",
            official_phone="+15550100002",
        ),
    }
    repo.homes = {
        "estate-main-12": _homes("estate-main-12", [None], 4, ["A", "B"]),
        "estate-elm-5": _homes("estate-elm-5", ["1", "2"], 3, ["A", "B", "C"]),
    }
    repo.administrator_config = {
        "adm-demo": PartialConfig(
            specialist_by_category={
                "plumbing": "spc-plumber",
                "electrical": "spc-electrician",
                "elevator": "spc-elevators",
                "locksmith": "spc-locksmith",
            },
        ),
    }
    # Id de la versión vigente de cada capa de administrador (como en `configs`)
    repo.administrator_config_id = {"adm-demo": "cfg-adm-demo-v1"}
    # Versión de configuración por finca: (id_version, documento)
    repo.estate_config = {
        "estate-main-12": (
            "cfg-main-12-v1",
            PartialConfig(
                contact=Contact(email="main12@demo.test", official_phone="+15550100001"),
            ),
        ),
        "estate-elm-5": (
            "cfg-elm-5-v1",
            PartialConfig(
                contact=Contact(email="elm5@demo.test", official_phone="+15550100002"),
                home_attempts=4,
            ),
        ),
    }


#: Language -> demo set. A language without its own set uses the English one
DEMOS = {"es": spanish, "en": english}


def load(repo: MemoryRepository, language: str) -> None:
    DEMOS.get(language, english)(repo)
