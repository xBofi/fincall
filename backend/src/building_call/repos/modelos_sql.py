"""Modelos SQLAlchemy (PostgreSQL).

Las configuraciones son JSONB versionadas: nunca se actualizan en sitio.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonDoc = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class AdministradorSQL(Base):
    __tablename__ = "administradores"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))


class EspecialistaSQL(Base):
    __tablename__ = "especialistas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrador_id: Mapped[str] = mapped_column(ForeignKey("administradores.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    categoria_principal: Mapped[str] = mapped_column(String(64))
    canal_contacto: Mapped[str] = mapped_column(String(16))
    destino: Mapped[str] = mapped_column(String(200))


class FincaSQL(Base):
    __tablename__ = "fincas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrador_id: Mapped[str] = mapped_column(ForeignKey("administradores.id"), index=True)
    direccion: Mapped[str] = mapped_column(String(300))
    correo: Mapped[str] = mapped_column(String(200))
    telefono_oficial: Mapped[str] = mapped_column(String(32))


class ViviendaSQL(Base):
    __tablename__ = "viviendas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    finca_id: Mapped[str] = mapped_column(ForeignKey("fincas.id"), index=True)
    portal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    piso: Mapped[str] = mapped_column(String(16))
    puerta: Mapped[str] = mapped_column(String(16))


class ConfiguracionSQL(Base):
    __tablename__ = "configuraciones"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ambito: Mapped[str] = mapped_column(String(16))  # administrador | finca
    ambito_id: Mapped[str] = mapped_column(String(64), index=True)
    documento: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    valido_desde: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LlamadaSQL(Base):
    __tablename__ = "llamadas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    iniciada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    telefono_llamante: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finca_id: Mapped[str | None] = mapped_column(ForeignKey("fincas.id"), nullable=True)
    vivienda_id: Mapped[str | None] = mapped_column(ForeignKey("viviendas.id"), nullable=True)
    nombre_declarado: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transcripcion: Mapped[list[dict[str, str]]] = mapped_column(JsonDoc, default=list)
    resumen: Mapped[str | None] = mapped_column(Text, nullable=True)
    resultado: Mapped[str | None] = mapped_column(String(40), nullable=True)
    motivo_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuracion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grafo: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IncidenciaSQL(Base):
    __tablename__ = "incidencias"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    llamada_id: Mapped[str] = mapped_column(ForeignKey("llamadas.id"), index=True)
    finca_id: Mapped[str] = mapped_column(ForeignKey("fincas.id"), index=True)
    vivienda_id: Mapped[str] = mapped_column(ForeignKey("viviendas.id"))
    descripcion: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str] = mapped_column(String(64))
    especialista_id: Mapped[str | None] = mapped_column(
        ForeignKey("especialistas.id"), nullable=True
    )
    urgencia: Mapped[str] = mapped_column(String(16))
    estado: Mapped[str] = mapped_column(String(32), default="pendiente")
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IncidenciaHistorialSQL(Base):
    __tablename__ = "incidencia_historial"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incidencia_id: Mapped[str] = mapped_column(ForeignKey("incidencias.id"), index=True)
    estado_anterior: Mapped[str | None] = mapped_column(String(32), nullable=True)
    estado_nuevo: Mapped[str] = mapped_column(String(32))
    cambiado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
