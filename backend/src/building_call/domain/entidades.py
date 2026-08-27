"""Entidades de negocio (ver bóveda: 03 Datos/Modelo de información)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from building_call.domain.configuracion import Urgencia

EstadoIncidencia = Literal[
    "pendiente", "asignada", "en_curso", "esperando_informacion", "resuelta", "cerrada"
]
ResultadoLlamada = Literal[
    "incidencia_creada",
    "finca_no_identificada",
    "vivienda_no_identificada",
    "no_clasificable",
    "abandonada",
]


class Administrador(BaseModel):
    id: str
    nombre: str


class Especialista(BaseModel):
    id: str
    administrador_id: str
    nombre: str
    categoria_principal: str
    canal_contacto: Literal["correo", "llamada"]
    destino: str


class Finca(BaseModel):
    id: str
    administrador_id: str
    direccion: str
    correo: str
    telefono_oficial: str


class Vivienda(BaseModel):
    id: str
    finca_id: str
    portal: str | None = None
    piso: str
    puerta: str

    def etiqueta(self) -> str:
        partes = [f"portal {self.portal}" if self.portal else None, f"{self.piso}º", self.puerta]
        return " ".join(p for p in partes if p)


class Llamada(BaseModel):
    id: str
    iniciada_en: datetime
    telefono_llamante: str | None = None
    finca_id: str | None = None
    vivienda_id: str | None = None
    nombre_declarado: str | None = None
    transcripcion: list[dict[str, str]] = Field(default_factory=list)
    resumen: str | None = None
    resultado: ResultadoLlamada | None = None
    motivo_revision: str | None = None
    configuracion_id: str | None = None
    grafo: str | None = None


class Incidencia(BaseModel):
    id: str
    llamada_id: str
    finca_id: str
    vivienda_id: str
    descripcion: str
    categoria: str
    especialista_id: str | None
    urgencia: Urgencia
    estado: EstadoIncidencia = "pendiente"
    creada_en: datetime
