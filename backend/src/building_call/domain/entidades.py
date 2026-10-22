"""Entidades de negocio (ver bóveda: 03 Datos/Modelo de información)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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
    "derivada_administrador",
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


#: Qué tipo de actuación pide la incidencia. Solo `reparacion` se avisa directamente a un
#: profesional; el resto lo decide el administrador (regla de Andreu, 2026-10-21)
Actuacion = Literal["reparacion", "reposicion", "siniestro", "convivencia", "privativo"]
ACTUACIONES: tuple[str, ...] = ("reparacion", "reposicion", "siniestro", "convivencia", "privativo")


class Equipo(BaseModel):
    """Instalación de la finca con quien la mantiene: se avisa a su mantenedor antes que a un
    profesional genérico de la categoría."""

    id: str
    finca_id: str
    tipo: str  # portero automático, ascensor, puerta de garaje...
    ubicacion: str | None = None
    marca: str | None = None
    modelo: str | None = None
    instalador: str | None = None
    mantenedor_id: str | None = None  # un Especialista del administrador
    contrato: str | None = None
    garantia_hasta: date | None = None

    def descripcion(self) -> str:
        """'portero automático Fermax Citymax (portal)' para el prompt y la sugerencia."""
        marca = " ".join(p for p in (self.marca, self.modelo) if p)
        texto = f"{self.tipo} {marca}".strip()
        return f"{texto} ({self.ubicacion})" if self.ubicacion else texto


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
    estado: Literal["activa", "finalizada"] = "activa"
    finalizada_en: datetime | None = None
    requiere_revision: bool = False


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
    equipo_id: str | None = None
    actuacion: Actuacion = "reparacion"
    # Para el administrador: por qué le llega y a quién se sugiere avisar
    sugerencia: str | None = None


#: Qué hizo alguien con la incidencia desde la consola
AccionIncidencia = Literal["asignar", "cambiar_estado", "nota"]


class EntradaHistorialIncidencia(BaseModel):
    """Una acción sobre la incidencia: quién, cuándo, de qué estado a cuál y por qué."""

    id: str
    incidencia_id: str
    accion: AccionIncidencia
    estado_anterior: EstadoIncidencia | None = None
    estado_nuevo: EstadoIncidencia
    usuario_id: str | None = None
    nota: str | None = None
    detalle: dict[str, Any] = Field(default_factory=dict)
    cambiado_en: datetime
