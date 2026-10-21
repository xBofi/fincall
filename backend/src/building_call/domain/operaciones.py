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


class EstadoAcceso(StrEnum):
    """Quien entra con Google o Microsoft no usa la consola hasta que el owner se lo concede."""

    sin_solicitar = "sin_solicitar"
    solicitado = "solicitado"
    concedido = "concedido"
    denegado = "denegado"


class Usuario(BaseModel):
    """Persona de la consola. Entra con Google o Microsoft: se la reconoce por (proveedor,
    sujeto). Los campos de contraseña son de las cuentas locales antiguas, que ya no entran."""

    id: str
    nombre_usuario: str
    nombre: str | None = None
    rol: Rol
    activo: bool = True
    email: str | None = None
    proveedor: str | None = None
    sujeto: str | None = None
    estado_acceso: EstadoAcceso = EstadoAcceso.concedido
    solicitado_en: datetime | None = None
    hash_contrasena: str | None = None
    debe_cambiar_contrasena: bool = False
    creado_en: datetime | None = None

    def puede_entrar(self) -> bool:
        return self.activo and self.estado_acceso is EstadoAcceso.concedido

    def publico(self) -> dict[str, Any]:
        """Representación segura para la API: sin hash ni identificador del proveedor."""
        return self.model_dump(
            mode="json", exclude={"hash_contrasena", "sujeto", "debe_cambiar_contrasena"}
        )


class PistaAudio(BaseModel):
    llamada_id: str
    canal: Literal["cliente", "asistente", "mezcla"]
    creada_en: datetime
    expira_en: datetime

    @model_validator(mode="after")
    def expira_despues_de_crearse(self) -> Self:
        if self.expira_en <= self.creada_en:
            raise ValueError("expira_en debe ser posterior a creada_en")
        return self
