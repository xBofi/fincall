"""Contrato de escritura del catálogo: fincas, viviendas, profesionales, equipos y la capa de
configuración que decide quién atiende cada categoría.

Complementa a `Repository` (lectura de negocio). Lo implementan `MemoryRepository` y
`PostgresRepository`. Las reglas de dominio (qué se puede borrar, quién puede mantener un
equipo) viven en `catalog_api.py`; el repositorio solo guarda y cuenta.
"""

from __future__ import annotations

from typing import Literal, Protocol

from building_call.domain.config import PartialConfig
from building_call.domain.entities import (
    Administrator,
    Equipment,
    Estate,
    Home,
    Specialist,
)

Scope = Literal["administrator", "estate"]

#: Columnas por las que una llamada o una incidencia citan al catálogo
ReferenceField = Literal["estate_id", "home_id", "specialist_id", "equipment_id"]


class CatalogRepository(Protocol):
    async def list_administrators(self) -> list[Administrator]: ...

    async def get_specialist(self, specialist_id: str) -> Specialist | None: ...

    async def save_estate(self, estate: Estate) -> None: ...

    async def delete_estate(self, estate_id: str) -> None:
        """Borra la finca con sus viviendas, equipos y capas de configuración."""
        ...

    async def save_home(self, home: Home) -> None: ...

    async def delete_home(self, home_id: str) -> None: ...

    async def save_specialist(self, specialist: Specialist) -> None: ...

    async def delete_specialist(self, specialist_id: str) -> None: ...

    async def save_equipment(self, equipment: Equipment) -> None: ...

    async def delete_equipment(self, equipment_id: str) -> None: ...

    async def config_of(
        self, scope: Scope, scope_id: str
    ) -> tuple[PartialConfig | None, str | None]:
        """La capa vigente (sin resolver) y el id de su versión."""
        ...

    async def save_config(
        self, scope: Scope, scope_id: str, layer: PartialConfig
    ) -> str:
        """Crea una versión nueva de la capa; la anterior se conserva. Devuelve su id."""
        ...

    async def count_references(self, field: ReferenceField, value: str) -> dict[str, int]:
        """Cuántas llamadas e incidencias citan ese dato: {'llamadas': n, 'incidencias': n}."""
        ...
