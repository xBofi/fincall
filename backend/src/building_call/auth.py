"""Primitivas de autenticación y sesiones revocables."""

from __future__ import annotations

from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from building_call.domain.operaciones import Rol, Usuario


def hash_token_sesion(token: str) -> str:
    """Devuelve el digest persistible de un token opaco de sesión."""
    return sha256(token.encode("utf-8")).hexdigest()


def usuario_desde_google(
    claims: dict[str, object],
    *,
    permitidos: set[str],
    owners: set[str],
    administradores: set[str],
    calidad: set[str],
) -> Usuario:
    """Convierte una identidad OIDC verificada en un usuario con acceso explícito."""
    email = str(claims.get("email", "")).strip().lower()
    google_sub = str(claims.get("sub", "")).strip()
    if not google_sub or not email or claims.get("email_verified") is not True:
        raise PermissionError("Identidad de Google incompleta o no verificada")
    if email not in permitidos:
        raise PermissionError("Cuenta de Google no autorizada")
    rol = (
        Rol.owner
        if email in owners
        else Rol.administrador
        if email in administradores
        else Rol.calidad
        if email in calidad
        else Rol.calidad
    )
    return Usuario(
        id=f"usr-{uuid5(NAMESPACE_URL, google_sub).hex[:24]}",
        google_sub=google_sub,
        email=email,
        rol=rol,
        activo=True,
    )
