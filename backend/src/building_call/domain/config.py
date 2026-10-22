"""Configuración de negocio por capas: defaults -> administrador -> finca.

Ver docs/superpowers/specs/2026-08-27-finca-configuration-and-graph-registry-design.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Urgency = Literal["low", "medium", "high", "emergency"]
Channel = Literal["email", "call", "ticket"]

URGENCIES: tuple[Urgency, ...] = ("low", "medium", "high", "emergency")
UNCLASSIFIABLE_CATEGORY = "unclassifiable"


class NotificationRule(BaseModel):
    urgency: Urgency
    channel: Channel
    # id de especialista, "administrador" o "coordinador"
    target: str


class Contact(BaseModel):
    email: str
    official_phone: str


class PartialConfig(BaseModel):
    """Capa de administrador o de finca: todo opcional, lo ausente se hereda."""

    graph: str | None = None
    category_catalog: list[str] | None = None
    specialist_by_category: dict[str, str] | None = None
    notice_rules: list[NotificationRule] | None = None
    contact: Contact | None = None
    estate_attempts: int | None = Field(default=None, ge=1)
    home_attempts: int | None = Field(default=None, ge=1)
    ask_detail: bool | None = None
    emergencies_enabled: bool | None = None


class EffectiveConfig(BaseModel):
    """Resultado de resolver las tres capas. Se inyecta en el estado del grafo."""

    graph: str = "standard"
    category_catalog: list[str]
    specialist_by_category: dict[str, str] = Field(default_factory=dict)
    notice_rules: list[NotificationRule] = Field(default_factory=list)
    contact: Contact | None = None
    estate_attempts: int = 3
    home_attempts: int = 3
    # Una pregunta de detalle sobre la incidencia antes del resumen (ayuda al técnico)
    ask_detail: bool = True
    emergencies_enabled: bool = False

    @model_validator(mode="after")
    def _consistent_categories(self) -> EffectiveConfig:
        unknown = set(self.specialist_by_category) - set(self.category_catalog)
        if unknown:
            raise ValueError(
                f"especialista_por_categoria referencia categorías fuera del catálogo: "
                f"{sorted(unknown)}"
            )
        return self

    def effective_urgency(self, urgency: Urgency) -> Urgency:
        """Mientras las emergencias estén desactivadas se tratan como `high`."""
        if urgency == "emergency" and not self.emergencies_enabled:
            return "high"
        return urgency

    def rule_for(self, urgency: Urgency) -> NotificationRule | None:
        urgency = self.effective_urgency(urgency)
        return next((r for r in self.notice_rules if r.urgency == urgency), None)


DEFAULTS = EffectiveConfig(
    category_catalog=[
        "plumbing",
        "electrical",
        "elevator",
        "cleaning",
        "locksmith",
        "masonry",
        "painting",
        "gardening",
        "general_maintenance",
        "administrative",
    ],
    notice_rules=[
        NotificationRule(urgency="low", channel="ticket", target="especialista"),
        NotificationRule(urgency="medium", channel="ticket", target="especialista"),
        NotificationRule(urgency="high", channel="email", target="coordinador"),
    ],
)


def resolve(
    administrator: PartialConfig | None = None,
    estate: PartialConfig | None = None,
    defaults: EffectiveConfig = DEFAULTS,
) -> EffectiveConfig:
    """Fusiona las capas: cada campo declarado en una capa inferior sobrescribe al superior."""
    data = defaults.model_dump()
    for layer in (administrator, estate):
        if layer is None:
            continue
        data.update(layer.model_dump(exclude_none=True))
    return EffectiveConfig.model_validate(data)
