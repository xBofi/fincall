"""Contrato del repositorio operativo: acceso, sesiones, expedientes, trazabilidad y audio.

Complementa a `Repository` (negocio). Lo implementan `MemoryRepository` y `PostgresRepository`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from building_call.domain.entities import Call
from building_call.domain.operations import User


class AccessSession:
    """Sesión revocable: se guarda el hash del token, nunca el token."""

    def __init__(
        self,
        id: str,
        user_id: str,
        token_hash: str,
        created_at: datetime,
        expires_at: datetime,
        revoked_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.token_hash = token_hash
        self.created_at = created_at
        self.expires_at = expires_at
        self.revoked_at = revoked_at

    def is_current(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now


class OperationalRepository(Protocol):
    # Acceso
    async def save_user(self, user: User) -> None: ...

    async def get_user(self, user_id: str) -> User | None: ...

    async def get_user_by_name(self, username: str) -> User | None: ...

    async def get_user_by_identity(
        self, provider: str, subject: str
    ) -> User | None: ...

    async def list_users(self) -> list[User]: ...

    async def delete_user(self, user_id: str) -> None: ...

    async def revoke_sessions_of(self, user_id: str, when: datetime) -> None: ...

    async def save_session(self, session: AccessSession) -> None: ...

    async def get_session_by_hash(self, token_hash: str) -> AccessSession | None: ...

    async def revoke_session(self, token_hash: str, when: datetime) -> None: ...

    async def renew_session(self, token_hash: str, expires_at: datetime) -> None: ...

    # Expedientes
    async def list_calls(
        self, state: str | None = None, limit: int = 100
    ) -> list[Call]: ...

    async def get_call(self, call_id: str) -> Call | None: ...

    # Trazabilidad por turno
    async def save_turn(self, call_id: str, turn: int, data: dict[str, Any]) -> None: ...

    async def list_turns(self, call_id: str) -> list[dict[str, Any]]: ...

    async def save_decision(
        self, call_id: str, turn: int, node: str, data: dict[str, Any]
    ) -> None: ...

    async def list_decisions(self, call_id: str) -> list[dict[str, Any]]: ...

    # Audio
    async def save_track(self, track: dict[str, Any]) -> None: ...

    async def list_tracks(self, call_id: str) -> list[dict[str, Any]]: ...

    async def get_track(self, track_id: str) -> dict[str, Any] | None: ...

    async def expired_tracks(self, now: datetime) -> list[dict[str, Any]]: ...

    async def mark_track_deleted(self, track_id: str, when: datetime) -> None: ...

    # Auditoría
    async def record_audit(
        self, user_id: str | None, action: str, resource: str, detail: dict[str, Any]
    ) -> None: ...

    async def list_audit(self, limit: int = 200) -> list[dict[str, Any]]: ...

    # ----------------------------------------------------------------- plantillas del asistente

    async def list_overridden_templates(self) -> dict[str, tuple[str, int]]: ...

    async def save_template(self, template_id: str, text: str, author: str | None) -> int: ...

    async def delete_template(self, template_id: str) -> None: ...

    async def template_history(self, template_id: str) -> list[dict[str, Any]]: ...

    # ----------------------------------------------------------------- casos del banco de pruebas

    async def save_test_bank_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """Crea el caso o, si ese turno ya estaba marcado para el mismo banco, lo corrige."""
        ...

    async def list_test_bank_cases(self, call_id: str | None = None) -> list[dict[str, Any]]: ...

    async def delete_test_bank_case(self, case_id: str) -> dict[str, Any] | None: ...
