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


class AdministratorSQL(Base):
    __tablename__ = "administrators"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class SpecialistSQL(Base):
    __tablename__ = "specialists"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[str] = mapped_column(ForeignKey("administrators.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    main_category: Mapped[str] = mapped_column(String(64))
    contact_channel: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(200))


class EstateSQL(Base):
    __tablename__ = "estates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[str] = mapped_column(ForeignKey("administrators.id"), index=True)
    address: Mapped[str] = mapped_column(String(300))
    email: Mapped[str] = mapped_column(String(200))
    official_phone: Mapped[str] = mapped_column(String(32))


class HomeSQL(Base):
    __tablename__ = "homes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    estate_id: Mapped[str] = mapped_column(ForeignKey("estates.id"), index=True)
    entrance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    floor: Mapped[str] = mapped_column(String(16))
    door: Mapped[str] = mapped_column(String(16))


class ConfigSQL(Base):
    __tablename__ = "configs"
    __table_args__ = (
        Index("ix_configs_scope_current", "scope", "scope_id", "valid_from"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))  # administrador | finca
    scope_id: Mapped[str] = mapped_column(String(64), index=True)
    document: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CallSQL(Base):
    __tablename__ = "calls"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    caller_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    estate_id: Mapped[str | None] = mapped_column(ForeignKey("estates.id"), nullable=True, index=True)
    home_id: Mapped[str | None] = mapped_column(ForeignKey("homes.id"), nullable=True)
    declared_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transcript: Mapped[list[dict[str, str]]] = mapped_column(JsonDoc, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="active", index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSQL(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_users_identity"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_state: Mapped[str] = mapped_column(String(32), default="granted")
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )


class SessionSQL(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallTurnSQL(Base):
    __tablename__ = "call_turns"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallDecisionSQL(Base):
    __tablename__ = "call_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(128))
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AudioTrackSQL(Base):
    __tablename__ = "audio_tracks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    mime: Mapped[str] = mapped_column(String(128))
    checksum: Mapped[str] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEventSQL(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EquipmentSQL(Base):
    __tablename__ = "equipment"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    estate_id: Mapped[str] = mapped_column(ForeignKey("estates.id"), index=True)
    kind: Mapped[str] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    installer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    maintainer_id: Mapped[str | None] = mapped_column(ForeignKey("specialists.id"), nullable=True)
    contract: Mapped[str | None] = mapped_column(String(120), nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class IncidentSQL(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    estate_id: Mapped[str] = mapped_column(ForeignKey("estates.id"), index=True)
    home_id: Mapped[str] = mapped_column(ForeignKey("homes.id"))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))
    specialist_id: Mapped[str | None] = mapped_column(
        ForeignKey("specialists.id"), nullable=True
    )
    urgency: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    equipment_id: Mapped[str | None] = mapped_column(ForeignKey("equipment.id"), nullable=True)
    work_type: Mapped[str] = mapped_column(String(32), default="repair")
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)


class CallStateSQL(Base):
    """Estado vivo de una llamada en curso (el `CallState` del grafo, tal cual, en JSONB).

    Una fila por llamada, sobrescrita en cada turno: permite que la llamada sobreviva a un
    reinicio de la API y que varias instancias la atiendan. El expediente (`calls`) es lo que
    queda; esto se purga al poco de terminar la llamada."""

    __tablename__ = "call_states"
    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IncidentHistorySQL(Base):
    __tablename__ = "incident_history"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str] = mapped_column(String(32))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Desde la bandeja del administrador (0010): quién actuó, qué hizo y con qué datos
    action: Mapped[str] = mapped_column(String(32), default="change_state")
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)


class TemplateSQL(Base):
    """Sobrescritura publicada de una plantilla. Solo existe fila si se ha editado."""

    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TemplateVersionSQL(Base):
    """Histórico: qué texto estuvo publicado, desde cuándo y por obra de quién."""

    __tablename__ = "template_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BankCaseSQL(Base):
    """Turno real del vecino que calidad marca como caso del banco de pruebas, con lo que el
    asistente debería haber entendido. Sin teléfono ni nombre del vecino."""

    __tablename__ = "bank_cases"
    __table_args__ = (
        UniqueConstraint("call_id", "turn", "bank", name="uq_test_bank_cases_turn"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    bank: Mapped[str] = mapped_column(String(32))
    node: Mapped[str] = mapped_column(String(128))
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    heard_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    utterance: Mapped[str] = mapped_column(Text)
    catalog: Mapped[bool] = mapped_column(Boolean, default=True)
    equipment_list: Mapped[list[Any] | None] = mapped_column(JsonDoc, nullable=True)
    expected: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
