"""Contratos de dominio para acceso y operaciones auditables."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, model_validator


class Rol(StrEnum):
    owner = "owner"
    administrador = "administrador"
    calidad = "calidad"


class Usuario(BaseModel):
    id: str
    google_sub: str
    email: str
    rol: Rol
    activo: bool


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
