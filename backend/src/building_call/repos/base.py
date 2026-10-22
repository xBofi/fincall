"""Contrato de acceso a datos. La lógica del grafo solo depende de este protocolo."""

from __future__ import annotations

from typing import Protocol

from building_call.domain.config import EffectiveConfig
from building_call.domain.entities import (
    Administrator,
    Call,
    Equipment,
    Estate,
    Home,
    Incident,
    IncidentHistoryEntry,
    Specialist,
)


class Repository(Protocol):
    async def list_estates(self) -> list[Estate]: ...

    async def get_estate(self, estate_id: str) -> Estate | None: ...

    async def get_administrator(self, administrator_id: str) -> Administrator | None: ...

    async def homes_of(self, estate_id: str) -> list[Home]: ...

    async def specialists_of(self, administrator_id: str) -> list[Specialist]: ...

    async def equipment_of(self, estate_id: str) -> list[Equipment]: ...

    async def effective_config(
        self, estate_id: str
    ) -> tuple[EffectiveConfig, str | None]:
        """Devuelve la configuración resuelta y el id de la versión de finca aplicada."""
        ...

    async def save_call(self, call: Call) -> None: ...

    async def save_incident(self, incident: Incident) -> None: ...

    async def list_incidents(self) -> list[Incident]: ...

    async def get_incident(self, incident_id: str) -> Incident | None: ...

    async def apply_incident_action(
        self, before: Incident, after: Incident, utterance: IncidentHistoryEntry
    ) -> bool:
        """Guarda `after` y su entrada de historial a la vez (todo o nada). Si la incidencia ya
        no está como `before` (estado o especialista), alguien actuó mientras tanto: no toca nada
        y devuelve False."""
        ...

    async def list_incident_history(
        self, incident_id: str
    ) -> list[IncidentHistoryEntry]:
        """Las acciones sobre una incidencia, de la más antigua a la más reciente."""
        ...
