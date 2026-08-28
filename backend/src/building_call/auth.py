"""Primitivas de autenticación y sesiones revocables."""

from __future__ import annotations

from hashlib import sha256


def hash_token_sesion(token: str) -> str:
    """Devuelve el digest persistible de un token opaco de sesión."""
    return sha256(token.encode("utf-8")).hexdigest()
