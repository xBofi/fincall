"""Contratos de dominio para acceso y operaciones auditables."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, model_validator


class Rol(StrEnum):
    owner = "owner"
    administrador = "administrador"
    calidad = "calidad"


class Usuario(BaseModel):
    """Usuario interno de la consola. La contraseña solo existe como hash (scrypt)."""

    id: str
    nombre_usuario: str
    nombre: str | None = None
    rol: Rol
    activo: bool = True
    hash_contrasena: str
    debe_cambiar_contrasena: bool = False
    creado_en: datetime | None = None

    def publico(self) -> dict[str, Any]:
        """Representación segura para la API: nunca incluye el hash."""
        return self.model_dump(mode="json", exclude={"hash_contrasena"})


class PistaAudio(BaseModel):
    llamada_id: str
    canal: Literal["cliente", "asistente"]
    creada_en: datetime
    expira_en: datetime

    @model_validator(mode="after")
    def expira_despues_de_crearse(self) -> Self:
        if self.expira_en <= self.creada_en:
            raise ValueError("expira_en debe ser posterior a creada_en")
        return self
