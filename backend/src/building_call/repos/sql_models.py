"""Modelos SQLAlchemy (PostgreSQL).

Las configuraciones son JSONB versionadas: nunca se actualizan en sitio.

Every business table inherits from `AuditedTable`: its static attributes tell the audit
triggers what is personal, what is secret and what to ignore (spec
`docs/superpowers/specs/2026-10-22-audit-trail-design.md`). Changing one needs a migration that
reinstalls the triggers (`audit/install.py`); the tests check it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
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


class AuditedTable(Base):
    """Base of every business table. A PostgreSQL trigger writes the four `audit_*` stamps with
    the actor of the transaction, so the application cannot forge them. The `audit_` prefix
    keeps them apart from the `created_at`/`updated_at` some tables manage themselves."""

    __abstract__ = True

    #: Write one `audit_changes` row per insert, update and delete
    AUDIT_CHANGES: ClassVar[bool] = True
    #: Fields that can identify a person (phone, e-mail, ID, address, voice, dictated text).
    #: Logged as a fingerprint, never the value; reading the record is logged in `audit_access`
    PERSONAL_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: Hashes and tokens: also logged as a fingerprint
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: A change that only touches these fields is not logged (a session renewing itself)
    IGNORED_FIELDS: ClassVar[tuple[str, ...]] = ()

    audit_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    audit_created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    audit_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    audit_updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    @classmethod
    def has_personal_data(cls) -> bool:
        return bool(cls.PERSONAL_FIELDS)


class AdministratorSQL(AuditedTable):
    __tablename__ = "administrators"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class SpecialistSQL(AuditedTable):
    __tablename__ = "specialists"
    PERSONAL_FIELDS = ("target",)  # a professional's contact may be a person's phone
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[str] = mapped_column(ForeignKey("administrators.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    main_category: Mapped[str] = mapped_column(String(64))
    contact_channel: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(200))


class EstateSQL(AuditedTable):
    __tablename__ = "estates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[str] = mapped_column(ForeignKey("administrators.id"), index=True)
    address: Mapped[str] = mapped_column(String(300))
    email: Mapped[str] = mapped_column(String(200))
    official_phone: Mapped[str] = mapped_column(String(32))


class HomeSQL(AuditedTable):
    __tablename__ = "homes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    estate_id: Mapped[str] = mapped_column(ForeignKey("estates.id"), index=True)
    entrance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    floor: Mapped[str] = mapped_column(String(16))
    door: Mapped[str] = mapped_column(String(16))


class ConfigSQL(AuditedTable):
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


class CallSQL(AuditedTable):
    __tablename__ = "calls"
    PERSONAL_FIELDS = ("caller_phone", "transcript", "summary")
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    caller_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    estate_id: Mapped[str | None] = mapped_column(ForeignKey("estates.id"), nullable=True, index=True)
    home_id: Mapped[str | None] = mapped_column(ForeignKey("homes.id"), nullable=True)
    transcript: Mapped[list[dict[str, str]]] = mapped_column(JsonDoc, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="active", index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSQL(AuditedTable):
    __tablename__ = "users"
    PERSONAL_FIELDS = ("email", "subject")
    SECRET_FIELDS = ("password_hash",)
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


class SessionSQL(AuditedTable):
    __tablename__ = "sessions"
    SECRET_FIELDS = ("token_hash",)
    IGNORED_FIELDS = ("expires_at",)  # renews itself while used
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallTurnSQL(AuditedTable):
    __tablename__ = "call_turns"
    PERSONAL_FIELDS = ("data",)  # what the caller said
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallDecisionSQL(AuditedTable):
    __tablename__ = "call_decisions"
    PERSONAL_FIELDS = ("data",)  # extracted address, home, problem
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(128))
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AudioTrackSQL(AuditedTable):
    __tablename__ = "audio_tracks"
    PERSONAL_FIELDS = ("object_key",)  # points to the recording of a voice
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


#: The audit tables themselves: they do not inherit from `AuditedTable` (the log does not
#: audit itself). The two new ones are append-only: a trigger rejects changing or deleting them.
AUDIT_TABLES = frozenset({"audit_events", "audit_changes", "audit_access"})


class AuditChangeSQL(Base):
    """One row per insert, update, delete or erasure in an audited table. Written by a trigger."""

    __tablename__ = "audit_changes"
    __table_args__ = (
        Index("ix_audit_changes_record", "table_name", "record_id", "occurred_at"),
        Index("ix_audit_changes_actor", "actor", "occurred_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    table_name: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    operation: Mapped[str] = mapped_column(String(16))  # insert | update | delete | erase
    actor: Mapped[str] = mapped_column(String(160))
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_id: Mapped[int] = mapped_column(BigInteger)
    #: Only the columns that changed; personal and secret fields as a fingerprint
    before: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True)


class AuditAccessSQL(Base):
    """Who read, listed, viewed, played or downloaded what. An open view can only be extended
    or closed; nothing else changes."""

    __tablename__ = "audit_access"
    __table_args__ = (
        Index("ix_audit_access_resource", "resource", "resource_id", "started_at"),
        Index("ix_audit_access_actor", "actor", "started_at"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(16))  # read | list | view | play | download
    resource: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor: Mapped[str] = mapped_column(String(160))
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict)


class EquipmentSQL(AuditedTable):
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


class IncidentSQL(AuditedTable):
    __tablename__ = "incidents"
    PERSONAL_FIELDS = ("description", "suggestion")  # dictated by the caller
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


class CallStateSQL(AuditedTable):
    """Estado vivo de una llamada en curso (el `CallState` del grafo, tal cual, en JSONB).

    Una fila por llamada, sobrescrita en cada turno: permite que la llamada sobreviva a un
    reinicio de la API y que varias instancias la atiendan. El expediente (`calls`) es lo que
    queda; esto se purga al poco de terminar la llamada."""

    __tablename__ = "call_states"
    # Overwritten on every turn and purged when the call ends: what is kept and audited is the
    # call record (`calls`, `call_turns`). It still gets the stamps.
    AUDIT_CHANGES = False
    PERSONAL_FIELDS = ("state",)
    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IncidentHistorySQL(AuditedTable):
    __tablename__ = "incident_history"
    PERSONAL_FIELDS = ("note", "detail")  # free text, and the notice sent with the address
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


class TemplateSQL(AuditedTable):
    """Sobrescritura publicada de una plantilla. Solo existe fila si se ha editado."""

    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TemplateVersionSQL(AuditedTable):
    """Histórico: qué texto estuvo publicado, desde cuándo y por obra de quién."""

    __tablename__ = "template_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BankCaseSQL(AuditedTable):
    """Turno real del vecino que calidad marca como caso del banco de pruebas, con lo que el
    asistente debería haber entendido. Sin teléfono ni nombre del vecino."""

    __tablename__ = "bank_cases"
    PERSONAL_FIELDS = ("utterance", "heard_text")
    __table_args__ = (
        UniqueConstraint("call_id", "turn", "bank", name="uq_bank_cases_turn"),
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
