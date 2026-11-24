"""Repositorio en memoria con datos de demostración.

Permite validar los agentes sin PostgreSQL. No es el modo de producción.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from building_call import i18n
from building_call.audit.erasure import CallNotErasable
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
from building_call.repos import demo_data
from building_call.repos.call_state import MemoryStateStore
from building_call.repos.catalog import ReferenceField, Scope
from building_call.repos.operational import AccessSession


class MemoryRepository:
    # Demo data, filled by `demo_data.load` in the language of the set
    administrators: dict[str, Administrator]
    specialists: dict[str, Specialist]
    equipment_list: dict[str, list[Equipment]]
    estates: dict[str, Estate]
    homes: dict[str, list[Home]]
    administrator_config: dict[str, PartialConfig]
    administrator_config_id: dict[str, str]
    estate_config: dict[str, tuple[str, PartialConfig]]

    def __init__(self, demo: str | None = None) -> None:
        """`demo`: language of the demo data set (`repos.demo_data`); None = `BC_LANGUAGE`."""
        # Estado vivo de las llamadas en curso (lo usa `CallManager`)
        self.call_state_store = MemoryStateStore()
        demo_data.load(self, demo or i18n.default_code())
        self.calls: dict[str, Call] = {}
        self.incidents: dict[str, Incident] = {}
        self.incident_history: list[IncidentHistoryEntry] = []
        # Versiones de configuración que la consola ha ido creando: nunca se pisa una anterior
        self.config_history: list[dict[str, Any]] = []

    async def list_estates(self) -> list[Estate]:
        return list(self.estates.values())

    async def get_estate(self, estate_id: str) -> Estate | None:
        return self.estates.get(estate_id)

    async def get_administrator(self, administrator_id: str) -> Administrator | None:
        return self.administrators.get(administrator_id)

    async def homes_of(self, estate_id: str) -> list[Home]:
        return list(self.homes.get(estate_id, []))

    async def specialists_of(self, administrator_id: str) -> list[Specialist]:
        return [e for e in self.specialists.values() if e.administrator_id == administrator_id]

    async def equipment_of(self, estate_id: str) -> list[Equipment]:
        return list(self.equipment_list.get(estate_id, []))

    async def effective_config(
        self, estate_id: str
    ) -> tuple[EffectiveConfig, str | None]:
        estate = self.estates[estate_id]
        version_id, cfg_estate = self.estate_config.get(estate_id, (None, None))
        cfg_admin = self.administrator_config.get(estate.administrator_id)
        return resolve(cfg_admin, cfg_estate), version_id

    async def save_call(self, call: Call) -> None:
        self.calls[call.id] = call

    async def save_incident(self, incident: Incident) -> None:
        self.incidents[incident.id] = incident

    async def list_incidents(self) -> list[Incident]:
        return list(self.incidents.values())

    async def get_incident(self, incident_id: str) -> Incident | None:
        return self.incidents.get(incident_id)

    async def apply_incident_action(
        self, before: Incident, after: Incident, utterance: IncidentHistoryEntry
    ) -> bool:
        current = self.incidents.get(before.id)
        if current is None or (current.state, current.specialist_id) != (
            before.state,
            before.specialist_id,
        ):
            return False
        self.incidents[after.id] = after
        self.incident_history.append(utterance)
        return True

    async def list_incident_history(
        self, incident_id: str
    ) -> list[IncidentHistoryEntry]:
        return [h for h in self.incident_history if h.incident_id == incident_id]

    # --------------------------------------------------------------- catálogo (escritura)

    async def list_administrators(self) -> list[Administrator]:
        return list(self.administrators.values())

    async def get_specialist(self, specialist_id: str) -> Specialist | None:
        return self.specialists.get(specialist_id)

    async def save_estate(self, estate: Estate) -> None:
        self.estates[estate.id] = estate

    async def delete_estate(self, estate_id: str) -> None:
        self.estates.pop(estate_id, None)
        self.homes.pop(estate_id, None)
        self.equipment_list.pop(estate_id, None)
        self.estate_config.pop(estate_id, None)

    async def save_home(self, home: Home) -> None:
        items = self.homes.setdefault(home.estate_id, [])
        items[:] = [v for v in items if v.id != home.id] + [home]

    async def delete_home(self, home_id: str) -> None:
        for items in self.homes.values():
            items[:] = [v for v in items if v.id != home_id]

    async def save_specialist(self, specialist: Specialist) -> None:
        self.specialists[specialist.id] = specialist

    async def delete_specialist(self, specialist_id: str) -> None:
        self.specialists.pop(specialist_id, None)

    async def save_equipment(self, equipment: Equipment) -> None:
        items = self.equipment_list.setdefault(equipment.estate_id, [])
        if any(e.id == equipment.id for e in items):
            items[:] = [equipment if e.id == equipment.id else e for e in items]
        else:
            items.append(equipment)

    async def delete_equipment(self, equipment_id: str) -> None:
        for items in self.equipment_list.values():
            items[:] = [e for e in items if e.id != equipment_id]

    async def config_of(
        self, scope: Scope, scope_id: str
    ) -> tuple[PartialConfig | None, str | None]:
        if scope == "administrator":
            return (
                self.administrator_config.get(scope_id),
                self.administrator_config_id.get(scope_id),
            )
        version_id, layer = self.estate_config.get(scope_id, (None, None))
        return layer, version_id

    async def save_config(
        self, scope: Scope, scope_id: str, layer: PartialConfig
    ) -> str:
        version_id = f"cfg-{uuid4().hex[:16]}"
        if scope == "administrator":
            self.administrator_config[scope_id] = layer
            self.administrator_config_id[scope_id] = version_id
        else:
            self.estate_config[scope_id] = (version_id, layer)
        self.config_history.append(
            {
                "id": version_id,
                "scope": scope,
                "scope_id": scope_id,
                "document": layer.model_dump(exclude_none=True),
                "valid_from": datetime.now(UTC).isoformat(),
            }
        )
        return version_id

    async def count_references(self, field: ReferenceField, value: str) -> dict[str, int]:
        calls = sum(1 for call in self.calls.values() if getattr(call, field, None) == value)
        incidents = sum(1 for i in self.incidents.values() if getattr(i, field, None) == value)
        return {"calls": calls, "incidents": incidents}

    # ------------------------------------------------------------- operativo (acceso y trazabilidad)

    def _operational(self) -> None:
        if not hasattr(self, "users"):
            self.users: dict[str, User] = {}
            self.sessions: dict[str, AccessSession] = {}
            self.turns: dict[str, list[dict[str, Any]]] = {}
            self.decisions: dict[str, list[dict[str, Any]]] = {}
            self.tracks: dict[str, dict[str, Any]] = {}
            self.audit: list[dict[str, Any]] = []
            self.templates: dict[str, dict[str, Any]] = {}
            self.template_versions_log: list[dict[str, Any]] = []
            self.bank_cases: dict[str, dict[str, Any]] = {}

    async def save_user(self, user: User) -> None:
        self._operational()
        self.users[user.id] = user

    async def get_user(self, user_id: str) -> User | None:
        self._operational()
        return self.users.get(user_id)

    async def get_user_by_name(self, username: str) -> User | None:
        self._operational()
        return next((u for u in self.users.values() if u.username == username), None)

    async def get_user_by_identity(self, provider: str, subject: str) -> User | None:
        self._operational()
        return next(
            (u for u in self.users.values() if (u.provider, u.subject) == (provider, subject)),
            None,
        )

    async def list_users(self) -> list[User]:
        self._operational()
        return list(self.users.values())

    async def delete_user(self, user_id: str) -> None:
        self._operational()
        self.users.pop(user_id, None)
        self.sessions = {h: s for h, s in self.sessions.items() if s.user_id != user_id}

    async def revoke_sessions_of(self, user_id: str, when: datetime) -> None:
        self._operational()
        for s in self.sessions.values():
            if s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = when

    async def save_session(self, session: AccessSession) -> None:
        self._operational()
        self.sessions[session.token_hash] = session

    async def get_session_by_hash(self, token_hash: str) -> AccessSession | None:
        self._operational()
        return self.sessions.get(token_hash)

    async def revoke_session(self, token_hash: str, when: datetime) -> None:
        self._operational()
        if token_hash in self.sessions:
            self.sessions[token_hash].revoked_at = when

    async def renew_session(self, token_hash: str, expires_at: datetime) -> None:
        self._operational()
        if token_hash in self.sessions:
            self.sessions[token_hash].expires_at = expires_at

    async def list_calls(self, state: str | None = None, limit: int = 100) -> list[Call]:
        rows = [call for call in self.calls.values() if state is None or call.state == state]
        rows.sort(key=lambda call: call.started_at, reverse=True)
        return rows[:limit]

    async def get_call(self, call_id: str) -> Call | None:
        return self.calls.get(call_id)

    async def erase_call(self, call_id: str) -> list[str]:
        self._operational()
        call = self.calls.get(call_id)
        if call is None:
            raise CallNotErasable(call_id)
        if call.state == "active":
            raise CallNotErasable(f"{call_id} is in progress")
        del self.calls[call_id]
        self.turns.pop(call_id, None)
        self.decisions.pop(call_id, None)
        tracks = [t for t in self.tracks.values() if t["call_id"] == call_id]
        for t in tracks:
            del self.tracks[t["id"]]
        self.bank_cases = {k: c for k, c in self.bank_cases.items() if c["call_id"] != call_id}
        await self.call_state_store.discard(call_id)
        incidents = {i.id for i in self.incidents.values() if i.call_id == call_id}
        for incident_id in incidents:
            self.incidents[incident_id] = self.incidents[incident_id].model_copy(
                update={"call_id": DELETED_CALL, "description": "", "suggestion": None}
            )
        self.incident_history = [
            h.model_copy(update={"note": None, "detail": {}}) if h.incident_id in incidents else h
            for h in self.incident_history
        ]
        for event in self.audit:
            if event["resource"] == f"call:{call_id}":
                event.update(resource=f"call:{DELETED_CALL}", detail={})
        return [t["object_key"] for t in tracks]

    async def save_turn(self, call_id: str, turn: int, data: dict[str, Any]) -> None:
        self._operational()
        items = self.turns.setdefault(call_id, [])
        items[:] = [t for t in items if t["turn"] != turn]
        items.append({"turn": turn, **data})

    async def list_turns(self, call_id: str) -> list[dict[str, Any]]:
        self._operational()
        return sorted(self.turns.get(call_id, []), key=lambda t: t["turn"])

    async def save_decision(
        self, call_id: str, turn: int, node: str, data: dict[str, Any]
    ) -> None:
        self._operational()
        self.decisions.setdefault(call_id, []).append({"turn": turn, "node": node, **data})

    async def list_decisions(self, call_id: str) -> list[dict[str, Any]]:
        self._operational()
        return list(self.decisions.get(call_id, []))

    async def save_track(self, track: dict[str, Any]) -> None:
        self._operational()
        self.tracks[track["id"]] = dict(track)

    async def list_tracks(self, call_id: str) -> list[dict[str, Any]]:
        self._operational()
        return [p for p in self.tracks.values() if p["call_id"] == call_id]

    async def get_track(self, track_id: str) -> dict[str, Any] | None:
        self._operational()
        return self.tracks.get(track_id)

    async def expired_tracks(self, now: datetime) -> list[dict[str, Any]]:
        self._operational()
        return [
            p for p in self.tracks.values() if p["expires_at"] <= now and not p.get("deleted_at")
        ]

    async def mark_track_deleted(self, track_id: str, when: datetime) -> None:
        self._operational()
        if track_id in self.tracks:
            self.tracks[track_id]["deleted_at"] = when

    async def record_audit(
        self, user_id: str | None, action: str, resource: str, detail: dict[str, Any]
    ) -> None:
        self._operational()
        # Como la clave foránea de Postgres: un evento solo puede citar a un usuario que existe
        if user_id is not None and user_id not in self.users:
            raise KeyError(f"auditoría de un usuario inexistente: {user_id}")
        self.audit.append(
            {
                "user_id": user_id,
                "action": action,
                "resource": resource,
                "detail": detail,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )

    async def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        self._operational()
        return list(reversed(self.audit))[:limit]

    # ----------------------------------------------------------------- plantillas del asistente

    async def list_overridden_templates(self) -> dict[str, tuple[str, int]]:
        self._operational()
        return {k: (v["text"], v["version"]) for k, v in self.templates.items()}

    async def save_template(self, template_id: str, text: str, author: str | None) -> int:
        self._operational()
        version: int = self.templates.get(template_id, {}).get("version", 0) + 1
        self.templates[template_id] = {"text": text, "version": version, "author": author}
        self.template_versions_log.append(
            {
                "template_id": template_id,
                "version": version,
                "text": text,
                "author": author,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return version

    async def delete_template(self, template_id: str) -> None:
        self._operational()
        self.templates.pop(template_id, None)

    async def template_history(self, template_id: str) -> list[dict[str, Any]]:
        self._operational()
        return [h for h in reversed(self.template_versions_log) if h["template_id"] == template_id]

    # ----------------------------------------------------------------- casos del banco de pruebas

    async def save_test_bank_case(self, case: dict[str, Any]) -> dict[str, Any]:
        self._operational()
        now = datetime.now(UTC).isoformat()
        key = (case["call_id"], case["turn"], case["bank"])
        previous = next(
            (
                c
                for c in self.bank_cases.values()
                if (c["call_id"], c["turn"], c["bank"]) == key
            ),
            None,
        )
        saved = {
            **case,
            "id": previous["id"] if previous else f"cb-{uuid4().hex[:16]}",
            "created_at": previous["created_at"] if previous else now,
            "updated_at": now,
        }
        self.bank_cases[saved["id"]] = saved
        return dict(saved)

    async def list_test_bank_cases(self, call_id: str | None = None) -> list[dict[str, Any]]:
        self._operational()
        return [
            dict(c)
            for c in sorted(self.bank_cases.values(), key=lambda c: (c["created_at"], c["id"]))
            if call_id is None or c["call_id"] == call_id
        ]

    async def delete_test_bank_case(self, case_id: str) -> dict[str, Any] | None:
        self._operational()
        return self.bank_cases.pop(case_id, None)
