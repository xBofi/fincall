"""Primitivas de autenticación: el token de sesión es opaco y en base de datos solo vive su hash.

Las contraseñas desaparecieron con el acceso por Google y Microsoft (Andreu, 2026-10-21).
"""

from __future__ import annotations

import hashlib


def hash_token_sesion(token: str) -> str:
    """Devuelve el digest persistible de un token opaco de sesión."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
