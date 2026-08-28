"""Contrato del repositorio operativo: acceso, sesiones, expedientes, trazabilidad y audio.

Complementa a `Repositorio` (negocio). Lo implementan `RepositorioMemoria` y `RepositorioPostgres`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from building_call.domain.entidades import Llamada
from building_call.domain.operaciones import Usuario


class SesionAcceso:
    """Sesión revocable: se guarda el hash del token, nunca el token."""

    def __init__(
        self,
        id: str,
        usuario_id: str,
        token_hash: str,
        creada_en: datetime,
        expira_en: datetime,
        revocada_en: datetime | None = None,
    ) -> None:
        self.id = id
        self.usuario_id = usuario_id
        self.token_hash = token_hash
        self.creada_en = creada_en
        self.expira_en = expira_en
        self.revocada_en = revocada_en

    def vigente(self, ahora: datetime) -> bool:
        return self.revocada_en is None and self.expira_en > ahora


class RepositorioOperativo(Protocol):
    # Acceso
    async def guardar_usuario(self, usuario: Usuario) -> None: ...

    async def obtener_usuario(self, usuario_id: str) -> Usuario | None: ...

    async def guardar_sesion(self, sesion: SesionAcceso) -> None: ...

    async def obtener_sesion_por_hash(self, token_hash: str) -> SesionAcceso | None: ...

    async def revocar_sesion(self, token_hash: str, cuando: datetime) -> None: ...

    # Expedientes
    async def listar_llamadas(
        self, estado: str | None = None, limite: int = 100
    ) -> list[Llamada]: ...

    async def obtener_llamada(self, llamada_id: str) -> Llamada | None: ...

    # Trazabilidad por turno
    async def guardar_turno(self, llamada_id: str, turno: int, datos: dict[str, Any]) -> None: ...

    async def listar_turnos(self, llamada_id: str) -> list[dict[str, Any]]: ...

    async def guardar_decision(
        self, llamada_id: str, turno: int, nodo: str, datos: dict[str, Any]
    ) -> None: ...

    async def listar_decisiones(self, llamada_id: str) -> list[dict[str, Any]]: ...

    # Audio
    async def guardar_pista(self, pista: dict[str, Any]) -> None: ...

    async def listar_pistas(self, llamada_id: str) -> list[dict[str, Any]]: ...

    async def obtener_pista(self, pista_id: str) -> dict[str, Any] | None: ...

    async def pistas_vencidas(self, ahora: datetime) -> list[dict[str, Any]]: ...

    async def marcar_pista_eliminada(self, pista_id: str, cuando: datetime) -> None: ...

    # Auditoría
    async def registrar_auditoria(
        self, usuario_id: str | None, accion: str, recurso: str, detalle: dict[str, Any]
    ) -> None: ...

    async def listar_auditoria(self, limite: int = 200) -> list[dict[str, Any]]: ...
