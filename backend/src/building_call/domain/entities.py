"""Entidades de negocio (ver bóveda: 03 Datos/Modelo de información)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from building_call.domain.config import Urgency

IncidentStatus = Literal[
    "pending", "assigned", "in_progress", "awaiting_information", "resolved", "closed"
]
CallResult = Literal[
    "incident_created",
    "estate_not_identified",
    "home_not_identified",
    "unclassifiable",
    "handed_to_administrator",
    "abandoned",
]


class Administrator(BaseModel):
    id: str
    name: str


class Specialist(BaseModel):
    id: str
    administrator_id: str
    name: str
    main_category: str
    contact_channel: Literal["email", "call"]
    target: str


class Estate(BaseModel):
    id: str
    administrator_id: str
    address: str
    email: str
    official_phone: str


#: Qué tipo de actuación pide la incidencia. Solo `repair` se avisa directamente a un
#: profesional; el resto lo decide el administrador (regla de Andreu, 2026-10-21)
WorkType = Literal["repair", "replacement", "insurance_claim", "neighbour_dispute", "private_unit"]
WORK_TYPES: tuple[str, ...] = ("repair", "replacement", "insurance_claim", "neighbour_dispute", "private_unit")


class Equipment(BaseModel):
    """Instalación de la finca con quien la mantiene: se avisa a su mantenedor antes que a un
    profesional genérico de la categoría."""

    id: str
    estate_id: str
    kind: str  # portero automático, ascensor, puerta de garaje...
    location: str | None = None
    brand: str | None = None
    model: str | None = None
    installer: str | None = None
    maintainer_id: str | None = None  # un Especialista del administrador
    contract: str | None = None
    warranty_until: date | None = None

    def description(self) -> str:
        """'portero automático Fermax Citymax (portal)' para el prompt y la sugerencia."""
        brand = " ".join(p for p in (self.brand, self.model) if p)
        text = f"{self.kind} {brand}".strip()
        return f"{text} ({self.location})" if self.location else text


class Home(BaseModel):
    id: str
    estate_id: str
    entrance: str | None = None
    floor: str
    door: str

    def label(self, language: str | None = None) -> str:
        """'3º B' / 'Apt 3B', as the language writes it; None = the active (or default) one."""
        from building_call import i18n

        code = language or i18n.active_code()
        return i18n.parser_for(code).home_label(self.entrance, self.floor, self.door)


class Call(BaseModel):
    id: str
    started_at: datetime
    caller_phone: str | None = None
    estate_id: str | None = None
    home_id: str | None = None
    transcript: list[dict[str, str]] = Field(default_factory=list)
    summary: str | None = None
    result: CallResult | None = None
    review_reason: str | None = None
    config_id: str | None = None
    graph: str | None = None
    state: Literal["active", "finished"] = "active"
    finished_at: datetime | None = None
    needs_review: bool = False


class Incident(BaseModel):
    id: str
    call_id: str
    estate_id: str
    home_id: str
    description: str
    category: str
    specialist_id: str | None
    urgency: Urgency
    state: IncidentStatus = "pending"
    created_at: datetime
    equipment_id: str | None = None
    work_type: WorkType = "repair"
    # Para el administrador: por qué le llega y a quién se sugiere avisar
    suggestion: str | None = None


#: Qué hizo alguien con la incidencia desde la consola
IncidentAction = Literal["assign", "change_state", "note"]


class IncidentHistoryEntry(BaseModel):
    """Una acción sobre la incidencia: quién, cuándo, de qué estado a cuál y por qué."""

    id: str
    incident_id: str
    action: IncidentAction
    previous_state: IncidentStatus | None = None
    new_state: IncidentStatus
    user_id: str | None = None
    note: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    changed_at: datetime
