"""Inicio de sesión con Google y Microsoft (OpenID Connect, código de autorización con PKCE).

Solo se confía en el `id_token` tras comprobar firma (JWKS del proveedor), emisor, audiencia,
caducidad y `nonce`. La persona se identifica por (proveedor, `sub`); el correo solo vale como
verificado cuando el proveedor lo garantiza (en Microsoft, cuentas personales o `xms_edov`).
Spec: `docs/superpowers/specs/2026-10-21-console-access-google-microsoft-design.md`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

#: Inquilino de las cuentas personales de Microsoft (outlook.com, hotmail, live)
TENANT_CUENTAS_PERSONALES = "9188040d-6c67-4c5b-b112-36a304b66dad"


class ErrorOidc(Exception):
    """El proveedor o el token no son válidos: el acceso se rechaza."""


@dataclass(frozen=True)
class Proveedor:
    nombre: str
    etiqueta: str
    autorizacion_url: str
    token_url: str
    jwks_url: str
    client_id: str
    client_secret: str

    def emisor_valido(self, claims: dict[str, Any]) -> bool:
        iss = claims.get("iss")
        if self.nombre == "google":
            return iss in ("https://accounts.google.com", "accounts.google.com")
        # Microsoft "common": el emisor lleva el inquilino del propio token
        tid = claims.get("tid")
        return bool(tid) and iss == f"https://login.microsoftonline.com/{tid}/v2.0"

    def correo_verificado(self, claims: dict[str, Any]) -> str | None:
        correo = claims.get("email")
        if not isinstance(correo, str) or not correo:
            return None
        if self.nombre == "google":
            return correo.lower() if claims.get("email_verified") is True else None
        # En un inquilino de empresa el correo lo escribe su administrador: no prueba nada
        if claims.get("tid") == TENANT_CUENTAS_PERSONALES or claims.get("xms_edov") is True:
            return correo.lower()
        return None


def proveedores_configurados(s: Any) -> dict[str, Proveedor]:
    salida: dict[str, Proveedor] = {}
    if s.google_client_id and s.google_client_secret:
        salida["google"] = Proveedor(
            "google",
            "Google",
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            "https://www.googleapis.com/oauth2/v3/certs",
            s.google_client_id,
            s.google_client_secret,
        )
    if s.microsoft_client_id and s.microsoft_client_secret:
        base = "https://login.microsoftonline.com/common"
        salida["microsoft"] = Proveedor(
            "microsoft",
            "Microsoft",
            f"{base}/oauth2/v2.0/authorize",
            f"{base}/oauth2/v2.0/token",
            f"{base}/discovery/v2.0/keys",
            s.microsoft_client_id,
            s.microsoft_client_secret,
        )
    return salida


@dataclass(frozen=True)
class Identidad:
    proveedor: str
    sujeto: str
    correo_verificado: str | None
    correo: str | None
    nombre: str | None


@dataclass(frozen=True)
class Intento:
    """Lo que se guarda (firmado) entre la ida al proveedor y la vuelta."""

    state: str
    nonce: str
    verificador: str

    @classmethod
    def nuevo(cls) -> Intento:
        return cls(secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48))

    @property
    def reto(self) -> str:
        digest = hashlib.sha256(self.verificador.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def url_autorizacion(p: Proveedor, intento: Intento, retorno: str) -> str:
    return (
        p.autorizacion_url
        + "?"
        + urlencode(
            {
                "client_id": p.client_id,
                "response_type": "code",
                "redirect_uri": retorno,
                "scope": "openid email profile",
                "state": intento.state,
                "nonce": intento.nonce,
                "code_challenge": intento.reto,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    )


def verificar_id_token(p: Proveedor, token: str, clave: Any, nonce: str) -> Identidad:
    try:
        claims = jwt.decode(
            token,
            clave,
            algorithms=["RS256"],
            audience=p.client_id,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=60,
        )
    except jwt.PyJWTError as e:
        raise ErrorOidc(f"id_token no válido: {e}") from e
    if not p.emisor_valido(claims):
        raise ErrorOidc("emisor del id_token no válido")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ErrorOidc("nonce del id_token no coincide")
    correo = claims.get("email") if isinstance(claims.get("email"), str) else None
    return Identidad(
        proveedor=p.nombre,
        sujeto=str(claims["sub"]),
        correo_verificado=p.correo_verificado(claims),
        correo=correo.lower() if correo else None,
        nombre=claims.get("name") if isinstance(claims.get("name"), str) else None,
    )


class ClienteOidc:
    """Habla con el proveedor: canjea el código y obtiene la clave pública del token."""

    def __init__(self) -> None:
        self._jwks: dict[str, jwt.PyJWKClient] = {}

    async def canjear(self, p: Proveedor, codigo: str, verificador: str, retorno: str) -> str:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                p.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": codigo,
                    "redirect_uri": retorno,
                    "client_id": p.client_id,
                    "client_secret": p.client_secret,
                    "code_verifier": verificador,
                },
                headers={"Accept": "application/json"},
            )
        if r.status_code != 200:
            raise ErrorOidc(f"el proveedor rechazó el código ({r.status_code})")
        token = r.json().get("id_token")
        if not isinstance(token, str):
            raise ErrorOidc("el proveedor no devolvió id_token")
        return token

    async def clave(self, p: Proveedor, token: str) -> Any:
        cliente = self._jwks.setdefault(p.nombre, jwt.PyJWKClient(p.jwks_url, cache_keys=True))
        try:
            # PyJWKClient descarga con urllib (bloqueante): fuera del bucle de eventos
            return (await asyncio.to_thread(cliente.get_signing_key_from_jwt, token)).key
        except jwt.PyJWTError as e:
            raise ErrorOidc(f"clave de firma desconocida: {e}") from e
