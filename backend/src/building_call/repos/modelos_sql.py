"""Modelos SQLAlchemy (PostgreSQL).

Las configuraciones son JSONB versionadas: nunca se actualizan en sitio.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    __table_args__ = (
        Index("ix_configuraciones_ambito_vigente", "ambito", "ambito_id", "valido_desde"),
    )
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
    iniciada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    telefono_llamante: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finca_id: Mapped[str | None] = mapped_column(ForeignKey("fincas.id"), nullable=True, index=True)
    vivienda_id: Mapped[str | None] = mapped_column(ForeignKey("viviendas.id"), nullable=True)
    nombre_declarado: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transcripcion: Mapped[list[dict[str, str]]] = mapped_column(JsonDoc, default=list)
    resumen: Mapped[str | None] = mapped_column(Text, nullable=True)
    resultado: Mapped[str | None] = mapped_column(String(40), nullable=True)
    motivo_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuracion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grafo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estado: Mapped[str] = mapped_column(String(32), default="activa", index=True)
    finalizada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requiere_revision: Mapped[bool] = mapped_column(Boolean, default=False)


class UsuarioSQL(Base):
    __tablename__ = "usuarios"
    __table_args__ = (UniqueConstraint("proveedor", "sujeto", name="uq_usuarios_identidad"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nombre_usuario: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    nombre: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rol: Mapped[str] = mapped_column(String(32))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    proveedor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sujeto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    estado_acceso: Mapped[str] = mapped_column(String(32), default="concedido")
    solicitado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hash_contrasena: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debe_cambiar_contrasena: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )


class SesionSQL(Base):
    __tablename__ = "sesiones"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revocada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlamadaTurnoSQL(Base):
    __tablename__ = "llamada_turnos"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    llamada_id: Mapped[str] = mapped_column(ForeignKey("llamadas.id"), index=True)
    turno: Mapped[int] = mapped_column(Integer)
    datos: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlamadaDecisionSQL(Base):
    __tablename__ = "llamada_decisiones"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    llamada_id: Mapped[str] = mapped_column(ForeignKey("llamadas.id"), index=True)
    turno: Mapped[int] = mapped_column(Integer)
    nodo: Mapped[str] = mapped_column(String(128))
    datos: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PistaAudioSQL(Base):
    __tablename__ = "pistas_audio"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    llamada_id: Mapped[str] = mapped_column(ForeignKey("llamadas.id"), index=True)
    canal: Mapped[str] = mapped_column(String(16))
    clave_objeto: Mapped[str] = mapped_column(String(512), unique=True)
    mime: Mapped[str] = mapped_column(String(128))
    checksum: Mapped[str] = mapped_column(String(128))
    duracion_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    eliminada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditoriaEventoSQL(Base):
    __tablename__ = "auditoria_eventos"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    accion: Mapped[str] = mapped_column(String(128))
    recurso: Mapped[str] = mapped_column(String(128))
    detalle: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)
    ocurrido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EquipoSQL(Base):
    __tablename__ = "equipos"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    finca_id: Mapped[str] = mapped_column(ForeignKey("fincas.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(120))
    ubicacion: Mapped[str | None] = mapped_column(String(200), nullable=True)
    marca: Mapped[str | None] = mapped_column(String(120), nullable=True)
    modelo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    instalador: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mantenedor_id: Mapped[str | None] = mapped_column(ForeignKey("especialistas.id"), nullable=True)
    contrato: Mapped[str | None] = mapped_column(String(120), nullable=True)
    garantia_hasta: Mapped[date | None] = mapped_column(Date, nullable=True)


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
    equipo_id: Mapped[str | None] = mapped_column(ForeignKey("equipos.id"), nullable=True)
    actuacion: Mapped[str] = mapped_column(String(32), default="reparacion")
    sugerencia: Mapped[str | None] = mapped_column(Text, nullable=True)


class EstadoLlamadaSQL(Base):
    """Estado vivo de una llamada en curso (el `EstadoLlamada` del grafo, tal cual, en JSONB).

    Una fila por llamada, sobrescrita en cada turno: permite que la llamada sobreviva a un
    reinicio de la API y que varias instancias la atiendan. El expediente (`llamadas`) es lo que
    queda; esto se purga al poco de terminar la llamada."""

    __tablename__ = "estados_llamada"
    llamada_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    estado: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    turno: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    finalizada: Mapped[bool] = mapped_column(Boolean, default=False)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


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
    # Desde la bandeja del administrador (0010): quién actuó, qué hizo y con qué datos
    accion: Mapped[str] = mapped_column(String(32), default="cambiar_estado")
    usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    detalle: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)


class PlantillaSQL(Base):
    """Sobrescritura publicada de una plantilla. Solo existe fila si se ha editado."""

    __tablename__ = "plantillas"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    texto: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    actualizada_por: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PlantillaVersionSQL(Base):
    """Histórico: qué texto estuvo publicado, desde cuándo y por obra de quién."""

    __tablename__ = "plantilla_versiones"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plantilla_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    texto: Mapped[str] = mapped_column(Text)
    autor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CasoBancoSQL(Base):
    """Turno real del vecino que calidad marca como caso del banco de pruebas, con lo que el
    asistente debería haber entendido. Sin teléfono ni nombre del vecino."""

    __tablename__ = "casos_banco"
    __table_args__ = (
        UniqueConstraint("llamada_id", "turno", "banco", name="uq_casos_banco_turno"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    llamada_id: Mapped[str] = mapped_column(ForeignKey("llamadas.id"), index=True)
    turno: Mapped[int] = mapped_column(Integer)
    banco: Mapped[str] = mapped_column(String(32))
    nodo: Mapped[str] = mapped_column(String(128))
    pregunta: Mapped[str | None] = mapped_column(Text, nullable=True)
    oido: Mapped[str | None] = mapped_column(Text, nullable=True)
    entrada: Mapped[str] = mapped_column(Text)
    catalogo: Mapped[bool] = mapped_column(Boolean, default=True)
    equipos: Mapped[list[Any] | None] = mapped_column(JsonDoc, nullable=True)
    esperado: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    autor_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
