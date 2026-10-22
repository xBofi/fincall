"""Repositorio PostgreSQL (SQLAlchemy async + asyncpg)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from building_call.audit import session as audit_session
from building_call.audit.erasure import erase_in_postgres
from building_call.audit.install import DELETED_CALL
from building_call.domain.config import (
    EffectiveConfig,
    PartialConfig,
    resolve,
)
from building_call.domain.entities import (
    Administrator,
    Call,
    Equipment,
    Estate,
    Home,
    Incident,
    IncidentHistoryEntry,
    Specialist,
)
from building_call.domain.operations import User
from building_call.repos.call_state import PostgresStateStore
from building_call.repos.catalog import ReferenceField, Scope
from building_call.repos.operational import AccessSession
from building_call.repos.sql_models import (
    AdministratorSQL,
    AudioTrackSQL,
    AuditEventSQL,
    BankCaseSQL,
    CallDecisionSQL,
    CallSQL,
    CallTurnSQL,
    ConfigSQL,
    EquipmentSQL,
    EstateSQL,
    HomeSQL,
    IncidentHistorySQL,
    IncidentSQL,
    SessionSQL,
    SpecialistSQL,
    TemplateSQL,
    TemplateVersionSQL,
    UserSQL,
)


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        # pre_ping: tras un reinicio de PostgreSQL las conexiones muertas se descartan en vez de
        # fallar en mitad de una llamada. Pool explícito: llamadas en curso + consola.
        self.engine = create_async_engine(
            database_url, pool_pre_ping=True, pool_size=10, max_overflow=20
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # Every transaction tells PostgreSQL who is acting, for the audit triggers
        audit_session.install()
        # Estado vivo de las llamadas en curso: sobrevive a un reinicio de la API
        self.call_state_store = PostgresStateStore(self.sessions)

    async def _latest_config(
        self, s: AsyncSession, scope: str, scope_id: str
    ) -> ConfigSQL | None:
        q = (
            select(ConfigSQL)
            .where(ConfigSQL.scope == scope, ConfigSQL.scope_id == scope_id)
            .order_by(ConfigSQL.valid_from.desc())
            .limit(1)
        )
        return (await s.execute(q)).scalar_one_or_none()

    async def list_estates(self) -> list[Estate]:
        async with self.sessions() as s:
            rows = (await s.execute(select(EstateSQL))).scalars()
            return [Estate.model_validate(f, from_attributes=True) for f in rows]

    async def get_estate(self, estate_id: str) -> Estate | None:
        async with self.sessions() as s:
            f = await s.get(EstateSQL, estate_id)
            return Estate.model_validate(f, from_attributes=True) if f else None

    async def get_administrator(self, administrator_id: str) -> Administrator | None:
        async with self.sessions() as s:
            a = await s.get(AdministratorSQL, administrator_id)
            return Administrator.model_validate(a, from_attributes=True) if a else None

    async def homes_of(self, estate_id: str) -> list[Home]:
        async with self.sessions() as s:
            q = select(HomeSQL).where(HomeSQL.estate_id == estate_id)
            rows = (await s.execute(q)).scalars()
            return [Home.model_validate(v, from_attributes=True) for v in rows]

    async def specialists_of(self, administrator_id: str) -> list[Specialist]:
        async with self.sessions() as s:
            q = select(SpecialistSQL).where(SpecialistSQL.administrator_id == administrator_id)
            rows = (await s.execute(q)).scalars()
            return [Specialist.model_validate(e, from_attributes=True) for e in rows]

    async def equipment_of(self, estate_id: str) -> list[Equipment]:
        async with self.sessions() as s:
            q = select(EquipmentSQL).where(EquipmentSQL.estate_id == estate_id)
            return [
                Equipment.model_validate(e, from_attributes=True)
                for e in (await s.execute(q)).scalars()
            ]

    async def effective_config(
        self, estate_id: str
    ) -> tuple[EffectiveConfig, str | None]:
        async with self.sessions() as s:
            estate = await s.get(EstateSQL, estate_id)
            if estate is None:
                raise KeyError(estate_id)
            cfg_admin = await self._latest_config(s, "administrator", estate.administrator_id)
            cfg_est = await self._latest_config(s, "estate", estate_id)
            effective = resolve(
                PartialConfig.model_validate(cfg_admin.document) if cfg_admin else None,
                PartialConfig.model_validate(cfg_est.document) if cfg_est else None,
            )
            return effective, (cfg_est.id if cfg_est else None)

    async def save_call(self, call: Call) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(CallSQL(**call.model_dump()))

    async def save_incident(self, incident: Incident) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(IncidentSQL(**incident.model_dump()))

    # --------------------------------------------------------------- catálogo (escritura)

    async def list_administrators(self) -> list[Administrator]:
        async with self.sessions() as s:
            rows = (await s.execute(select(AdministratorSQL))).scalars()
            return [Administrator.model_validate(a, from_attributes=True) for a in rows]

    async def get_specialist(self, specialist_id: str) -> Specialist | None:
        async with self.sessions() as s:
            e = await s.get(SpecialistSQL, specialist_id)
            return Specialist.model_validate(e, from_attributes=True) if e else None

    async def save_estate(self, estate: Estate) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(EstateSQL(**estate.model_dump()))

    async def delete_estate(self, estate_id: str) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(delete(EquipmentSQL).where(EquipmentSQL.estate_id == estate_id))
            await s.execute(delete(HomeSQL).where(HomeSQL.estate_id == estate_id))
            await s.execute(
                delete(ConfigSQL).where(
                    ConfigSQL.scope == "estate", ConfigSQL.scope_id == estate_id
                )
            )
            await s.execute(delete(EstateSQL).where(EstateSQL.id == estate_id))

    async def save_home(self, home: Home) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(HomeSQL(**home.model_dump()))

    async def delete_home(self, home_id: str) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(delete(HomeSQL).where(HomeSQL.id == home_id))

    async def save_specialist(self, specialist: Specialist) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(SpecialistSQL(**specialist.model_dump()))

    async def delete_specialist(self, specialist_id: str) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(delete(SpecialistSQL).where(SpecialistSQL.id == specialist_id))

    async def save_equipment(self, equipment: Equipment) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(EquipmentSQL(**equipment.model_dump()))

    async def delete_equipment(self, equipment_id: str) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(delete(EquipmentSQL).where(EquipmentSQL.id == equipment_id))

    async def config_of(
        self, scope: Scope, scope_id: str
    ) -> tuple[PartialConfig | None, str | None]:
        async with self.sessions() as s:
            row = await self._latest_config(s, scope, scope_id)
            if row is None:
                return None, None
            return PartialConfig.model_validate(row.document), row.id

    async def save_config(
        self, scope: Scope, scope_id: str, layer: PartialConfig
    ) -> str:
        version_id = f"cfg-{uuid4().hex[:16]}"
        async with self.sessions() as s, s.begin():
            s.add(
                ConfigSQL(
                    id=version_id,
                    scope=scope,
                    scope_id=scope_id,
                    document=layer.model_dump(exclude_none=True),
                    # Explícito: `now()` es el inicio de la transacción y dos versiones
                    # seguidas empatarían al buscar la vigente
                    valid_from=datetime.now(UTC),
                )
            )
        return version_id

    async def count_references(self, field: ReferenceField, value: str) -> dict[str, int]:
        async with self.sessions() as s:
            calls = 0
            call_column = getattr(CallSQL, field, None)
            if call_column is not None:
                q = select(func.count()).select_from(CallSQL).where(call_column == value)
                calls = (await s.execute(q)).scalar_one()
            q = (
                select(func.count())
                .select_from(IncidentSQL)
                .where(getattr(IncidentSQL, field) == value)
            )
            incidents = (await s.execute(q)).scalar_one()
        return {"calls": int(calls), "incidents": int(incidents)}

    # ------------------------------------------------------------- operativo (acceso y trazabilidad)

    async def save_user(self, user: User) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(UserSQL(**user.model_dump()))

    async def get_user(self, user_id: str) -> User | None:
        async with self.sessions() as s:
            u = await s.get(UserSQL, user_id)
            return User.model_validate(u, from_attributes=True) if u else None

    async def get_user_by_name(self, username: str) -> User | None:
        async with self.sessions() as s:
            u = (
                await s.execute(
                    select(UserSQL).where(UserSQL.username == username)
                )
            ).scalar_one_or_none()
            return User.model_validate(u, from_attributes=True) if u else None

    async def get_user_by_identity(self, provider: str, subject: str) -> User | None:
        async with self.sessions() as s:
            u = (
                await s.execute(
                    select(UserSQL).where(
                        UserSQL.provider == provider, UserSQL.subject == subject
                    )
                )
            ).scalar_one_or_none()
            return User.model_validate(u, from_attributes=True) if u else None

    async def list_users(self) -> list[User]:
        async with self.sessions() as s:
            rows = (await s.execute(select(UserSQL))).scalars()
            return [User.model_validate(u, from_attributes=True) for u in rows]

    async def delete_user(self, user_id: str) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(delete(SessionSQL).where(SessionSQL.user_id == user_id))
            await s.execute(delete(UserSQL).where(UserSQL.id == user_id))

    async def revoke_sessions_of(self, user_id: str, when: datetime) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(
                update(SessionSQL)
                .where(SessionSQL.user_id == user_id, SessionSQL.revoked_at.is_(None))
                .values(revoked_at=when)
            )

    async def save_session(self, session: AccessSession) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(
                SessionSQL(
                    id=session.id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    revoked_at=session.revoked_at,
                )
            )

    async def get_session_by_hash(self, token_hash: str) -> AccessSession | None:
        async with self.sessions() as s:
            row = (
                await s.execute(select(SessionSQL).where(SessionSQL.token_hash == token_hash))
            ).scalar_one_or_none()
            if row is None:
                return None
            return AccessSession(
                row.id,
                row.user_id,
                row.token_hash,
                row.created_at,
                row.expires_at,
                row.revoked_at,
            )

    async def revoke_session(self, token_hash: str, when: datetime) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(
                update(SessionSQL)
                .where(SessionSQL.token_hash == token_hash)
                .values(revoked_at=when)
            )

    async def renew_session(self, token_hash: str, expires_at: datetime) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(
                update(SessionSQL)
                .where(SessionSQL.token_hash == token_hash)
                .values(expires_at=expires_at)
            )

    async def list_calls(self, state: str | None = None, limit: int = 100) -> list[Call]:
        async with self.sessions() as s:
            q = (
                select(CallSQL)
                .where(CallSQL.id != DELETED_CALL)
                .order_by(CallSQL.started_at.desc())
                .limit(limit)
            )
            if state:
                q = q.where(CallSQL.state == state)
            return [
                Call.model_validate(f, from_attributes=True)
                for f in (await s.execute(q)).scalars()
            ]

    async def get_call(self, call_id: str) -> Call | None:
        if call_id == DELETED_CALL:
            return None
        async with self.sessions() as s:
            f = await s.get(CallSQL, call_id)
            return Call.model_validate(f, from_attributes=True) if f else None

    async def erase_call(self, call_id: str) -> list[str]:
        async with self.sessions() as s, s.begin():
            return await erase_in_postgres(s, call_id)

    async def save_turn(self, call_id: str, turn: int, data: dict[str, Any]) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(
                CallTurnSQL(
                    id=f"{call_id}:{turn}", call_id=call_id, turn=turn, data=data
                )
            )

    async def list_turns(self, call_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            q = (
                select(CallTurnSQL)
                .where(CallTurnSQL.call_id == call_id)
                .order_by(CallTurnSQL.turn)
            )
            return [{"turn": f.turn, **f.data} for f in (await s.execute(q)).scalars()]

    async def save_decision(
        self, call_id: str, turn: int, node: str, data: dict[str, Any]
    ) -> None:
        async with self.sessions() as s, s.begin():
            s.add(
                CallDecisionSQL(
                    id=f"dec-{uuid4().hex[:16]}",
                    call_id=call_id,
                    turn=turn,
                    node=node,
                    data=data,
                )
            )

    async def list_decisions(self, call_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            q = (
                select(CallDecisionSQL)
                .where(CallDecisionSQL.call_id == call_id)
                .order_by(CallDecisionSQL.turn, CallDecisionSQL.created_at)
            )
            return [
                {"turn": f.turn, "node": f.node, **f.data}
                for f in (await s.execute(q)).scalars()
            ]

    async def save_track(self, track: dict[str, Any]) -> None:
        async with self.sessions() as s, s.begin():
            await s.merge(AudioTrackSQL(**track))

    def _track_dict(self, f: AudioTrackSQL) -> dict[str, Any]:
        return {
            c: getattr(f, c)
            for c in (
                "id", "call_id", "channel", "object_key", "mime", "checksum", "duration_ms",
                "created_at", "expires_at", "deleted_at",
            )
        }  # fmt: skip

    async def list_tracks(self, call_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            q = select(AudioTrackSQL).where(AudioTrackSQL.call_id == call_id)
            return [self._track_dict(f) for f in (await s.execute(q)).scalars()]

    async def get_track(self, track_id: str) -> dict[str, Any] | None:
        async with self.sessions() as s:
            f = await s.get(AudioTrackSQL, track_id)
            return self._track_dict(f) if f else None

    async def expired_tracks(self, now: datetime) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            q = select(AudioTrackSQL).where(
                AudioTrackSQL.expires_at <= now, AudioTrackSQL.deleted_at.is_(None)
            )
            return [self._track_dict(f) for f in (await s.execute(q)).scalars()]

    async def mark_track_deleted(self, track_id: str, when: datetime) -> None:
        async with self.sessions() as s, s.begin():
            await s.execute(
                update(AudioTrackSQL)
                .where(AudioTrackSQL.id == track_id)
                .values(deleted_at=when)
            )

    async def record_audit(
        self, user_id: str | None, action: str, resource: str, detail: dict[str, Any]
    ) -> None:
        async with self.sessions() as s, s.begin():
            s.add(
                AuditEventSQL(
                    id=f"aud-{uuid4().hex[:16]}",
                    user_id=user_id,
                    action=action,
                    resource=resource,
                    detail=detail,
                )
            )

    async def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            q = (
                select(AuditEventSQL)
                .order_by(AuditEventSQL.occurred_at.desc())
                .limit(limit)
            )
            return [
                {
                    "user_id": f.user_id,
                    "action": f.action,
                    "resource": f.resource,
                    "detail": f.detail,
                    "occurred_at": f.occurred_at.isoformat() if f.occurred_at else None,
                }
                for f in (await s.execute(q)).scalars()
            ]

    async def list_incidents(self) -> list[Incident]:
        async with self.sessions() as s:
            rows = (await s.execute(select(IncidentSQL))).scalars()
            return [Incident.model_validate(i, from_attributes=True) for i in rows]

    async def get_incident(self, incident_id: str) -> Incident | None:
        async with self.sessions() as s:
            i = await s.get(IncidentSQL, incident_id)
            return Incident.model_validate(i, from_attributes=True) if i else None

    async def apply_incident_action(
        self, before: Incident, after: Incident, utterance: IncidentHistoryEntry
    ) -> bool:
        async with self.sessions() as s, s.begin():
            # Bloquea la fila: dos decisiones a la vez sobre la misma incidencia se serializan
            current = await s.get(IncidentSQL, before.id, with_for_update=True)
            if current is None or (current.state, current.specialist_id) != (
                before.state,
                before.specialist_id,
            ):
                return False
            current.state = after.state
            current.specialist_id = after.specialist_id
            s.add(IncidentHistorySQL(**utterance.model_dump()))
            return True

    async def list_incident_history(
        self, incident_id: str
    ) -> list[IncidentHistoryEntry]:
        async with self.sessions() as s:
            q = (
                select(IncidentHistorySQL)
                .where(IncidentHistorySQL.incident_id == incident_id)
                .order_by(IncidentHistorySQL.changed_at, IncidentHistorySQL.id)
            )
            return [
                IncidentHistoryEntry.model_validate(h, from_attributes=True)
                for h in (await s.execute(q)).scalars()
            ]

    # ----------------------------------------------------------------- plantillas del asistente

    async def list_overridden_templates(self) -> dict[str, tuple[str, int]]:
        async with self.sessions() as s:
            rows = (await s.execute(select(TemplateSQL))).scalars().all()
        return {f.id: (f.text, f.version) for f in rows}

    async def save_template(self, template_id: str, text: str, author: str | None) -> int:
        async with self.sessions() as s, s.begin():
            current = await s.get(TemplateSQL, template_id)
            version = (current.version if current else 0) + 1
            if current is None:
                s.add(
                    TemplateSQL(
                        id=template_id, text=text, version=version, updated_by=author
                    )
                )
            else:
                current.text = text
                current.version = version
                current.updated_by = author
                current.updated_at = datetime.now(UTC)
            s.add(
                TemplateVersionSQL(
                    id=f"plv-{uuid4().hex[:16]}",
                    template_id=template_id,
                    version=version,
                    text=text,
                    author=author,
                )
            )
        return version

    async def delete_template(self, template_id: str) -> None:
        async with self.sessions() as s, s.begin():
            current = await s.get(TemplateSQL, template_id)
            if current is not None:
                await s.delete(current)

    async def template_history(self, template_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            rows = (
                (
                    await s.execute(
                        select(TemplateVersionSQL)
                        .where(TemplateVersionSQL.template_id == template_id)
                        .order_by(TemplateVersionSQL.version.desc())
                    )
                )
                .scalars()
                .all()
            )
        return [
            {
                "version": f.version,
                "text": f.text,
                "author": f.author,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in rows
        ]

    # ----------------------------------------------------------------- casos del banco de pruebas

    _CASE_FIELDS = (
        "id", "call_id", "turn", "bank", "node", "question", "heard_text", "utterance", "catalog",
        "equipment_list", "expected", "note", "author_id",
    )  # fmt: skip

    def _case_dict(self, f: BankCaseSQL) -> dict[str, Any]:
        return {
            **{c: getattr(f, c) for c in self._CASE_FIELDS},
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        }

    async def save_test_bank_case(self, case: dict[str, Any]) -> dict[str, Any]:
        data = {c: case.get(c) for c in self._CASE_FIELDS if c != "id"}
        async with self.sessions() as s, s.begin():
            previous = (
                await s.execute(
                    select(BankCaseSQL).where(
                        BankCaseSQL.call_id == case["call_id"],
                        BankCaseSQL.turn == case["turn"],
                        BankCaseSQL.bank == case["bank"],
                    )
                )
            ).scalar_one_or_none()
            if previous is None:
                row = BankCaseSQL(id=f"cb-{uuid4().hex[:16]}", **data)
                s.add(row)
            else:
                row = previous
                for c, v in data.items():
                    setattr(row, c, v)
                row.updated_at = datetime.now(UTC)
            await s.flush()
            await s.refresh(row)
            return self._case_dict(row)

    async def list_test_bank_cases(self, call_id: str | None = None) -> list[dict[str, Any]]:
        q = select(BankCaseSQL).order_by(BankCaseSQL.created_at, BankCaseSQL.id)
        if call_id is not None:
            q = q.where(BankCaseSQL.call_id == call_id)
        async with self.sessions() as s:
            return [self._case_dict(f) for f in (await s.execute(q)).scalars()]

    async def delete_test_bank_case(self, case_id: str) -> dict[str, Any] | None:
        async with self.sessions() as s, s.begin():
            row = await s.get(BankCaseSQL, case_id)
            if row is None:
                return None
            case = self._case_dict(row)
            await s.delete(row)
            return case
