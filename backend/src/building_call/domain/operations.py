"""Contratos de dominio para acceso y operaciones auditables."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, model_validator


class Role(StrEnum):
    owner = "owner"
    administrator = "administrator"
    quality = "quality"


class AccessState(StrEnum):
    """Quien entra con Google o Microsoft no usa la consola hasta que el owner se lo concede."""

    not_requested = "not_requested"
    requested = "requested"
    granted = "granted"
    denied = "denied"


class User(BaseModel):
    """Persona de la consola. Entra con Google o Microsoft: se la reconoce por (proveedor,
    sujeto). Los campos de contraseña son de las cuentas locales antiguas, que ya no entran."""

    id: str
    username: str
    name: str | None = None
    role: Role
    active: bool = True
    email: str | None = None
    provider: str | None = None
    subject: str | None = None
    access_state: AccessState = AccessState.granted
    requested_at: datetime | None = None
    password_hash: str | None = None
    must_change_password: bool = False
    created_at: datetime | None = None

    def can_sign_in(self) -> bool:
        return self.active and self.access_state is AccessState.granted

    def public(self) -> dict[str, Any]:
        """Representación segura para la API: sin hash ni identificador del proveedor."""
        return self.model_dump(
            mode="json", exclude={"password_hash", "subject", "must_change_password"}
        )


class AudioTrack(BaseModel):
    call_id: str
    channel: Literal["client", "assistant", "mix"]
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def expires_after_creation(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("expira_en debe ser posterior a creada_en")
        return self
