"""Primitivas de autenticación: hash de contraseñas (scrypt), tokens de sesión y política.

Sin dependencias externas: `hashlib.scrypt` (OpenSSL) con sal aleatoria por contraseña y
comparación en tiempo constante. Formato persistido: ``scrypt$n$r$p$sal_hex$hash_hex``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_SCRYPT_N = 2**14  # 16 MiB de memoria; decenas de ms por verificación en CPU actual
_SCRYPT_MAXMEM = 64 * 1024 * 1024  # OpenSSL limita a 32 MiB por defecto
_SCRYPT_R = 8
_SCRYPT_P = 1
LONGITUD_MINIMA_CONTRASENA = 10
_NOMBRE_USUARIO = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def hash_token_sesion(token: str) -> str:
    """Devuelve el digest persistible de un token opaco de sesión."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_contrasena(contrasena: str) -> str:
    sal = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        contrasena.encode("utf-8"),
        salt=sal,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${sal.hex()}${digest.hex()}"


def verificar_contrasena(contrasena: str, almacenado: str) -> bool:
    try:
        esquema, n, r, p, sal_hex, hash_hex = almacenado.split("$")
        if esquema != "scrypt":
            return False
        digest = hashlib.scrypt(
            contrasena.encode("utf-8"),
            salt=bytes.fromhex(sal_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(hash_hex)),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, bytes.fromhex(hash_hex))


def generar_contrasena_temporal() -> str:
    """Contraseña de un solo uso para altas y restablecimientos: el usuario debe cambiarla."""
    return secrets.token_urlsafe(12)


def validar_nombre_usuario(nombre: str) -> str:
    nombre = nombre.strip().lower()
    if not _NOMBRE_USUARIO.match(nombre):
        raise ValueError(
            "El nombre de usuario debe tener 2-64 caracteres: letras minúsculas, números, '.', '_' o '-'"
        )
    return nombre


def validar_contrasena_nueva(contrasena: str, nombre_usuario: str) -> None:
    """Política mínima para contraseñas elegidas por personas (las temporales la evitan)."""
    if len(contrasena) < LONGITUD_MINIMA_CONTRASENA:
        raise ValueError(
            f"La contraseña debe tener al menos {LONGITUD_MINIMA_CONTRASENA} caracteres"
        )
    if contrasena.lower() == nombre_usuario.lower():
        raise ValueError("La contraseña no puede ser el nombre de usuario")
    if contrasena.lower() in {"admin", "administrador", "contrasena", "contraseña", "password"}:
        raise ValueError("Contraseña demasiado común")
