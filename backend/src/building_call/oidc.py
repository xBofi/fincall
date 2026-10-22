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
PERSONAL_ACCOUNTS_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


class OidcError(Exception):
    """El proveedor o el token no son válidos: el acceso se rechaza."""


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorization_url: str
    token_url: str
    jwks_url: str
    client_id: str
    client_secret: str

    def valid_issuer(self, claims: dict[str, Any]) -> bool:
        iss = claims.get("iss")
        if self.name == "google":
            return iss in ("https://accounts.google.com", "accounts.google.com")
        # Microsoft "common": el emisor lleva el inquilino del propio token
        tid = claims.get("tid")
        return bool(tid) and iss == f"https://login.microsoftonline.com/{tid}/v2.0"

    def verified_email(self, claims: dict[str, Any]) -> str | None:
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            return None
        if self.name == "google":
            return email.lower() if claims.get("email_verified") is True else None
        # En un inquilino de empresa el correo lo escribe su administrador: no prueba nada
        if claims.get("tid") == PERSONAL_ACCOUNTS_TENANT or claims.get("xms_edov") is True:
            return email.lower()
        return None


def configured_providers(s: Any) -> dict[str, Provider]:
    output: dict[str, Provider] = {}
    if s.google_client_id and s.google_client_secret:
        output["google"] = Provider(
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
        output["microsoft"] = Provider(
            "microsoft",
            "Microsoft",
            f"{base}/oauth2/v2.0/authorize",
            f"{base}/oauth2/v2.0/token",
            f"{base}/discovery/v2.0/keys",
            s.microsoft_client_id,
            s.microsoft_client_secret,
        )
    return output


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str
    verified_email: str | None
    email: str | None
    name: str | None


@dataclass(frozen=True)
class Attempt:
    """Lo que se guarda (firmado) entre la ida al proveedor y la vuelta."""

    state: str
    nonce: str
    verifier: str

    @classmethod
    def fresh(cls) -> Attempt:
        return cls(secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48))

    @property
    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(p: Provider, attempt: Attempt, callback: str) -> str:
    return (
        p.authorization_url
        + "?"
        + urlencode(
            {
                "client_id": p.client_id,
                "response_type": "code",
                "redirect_uri": callback,
                "scope": "openid email profile",
                "state": attempt.state,
                "nonce": attempt.nonce,
                "code_challenge": attempt.challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    )


def verify_id_token(p: Provider, token: str, key: Any, nonce: str) -> Identity:
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=p.client_id,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=60,
        )
    except jwt.PyJWTError as e:
        raise OidcError(f"id_token no válido: {e}") from e
    if not p.valid_issuer(claims):
        raise OidcError("emisor del id_token no válido")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("nonce del id_token no coincide")
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    return Identity(
        provider=p.name,
        subject=str(claims["sub"]),
        verified_email=p.verified_email(claims),
        email=email.lower() if email else None,
        name=claims.get("name") if isinstance(claims.get("name"), str) else None,
    )


class OidcClient:
    """Habla con el proveedor: canjea el código y obtiene la clave pública del token."""

    def __init__(self) -> None:
        self._jwks: dict[str, jwt.PyJWKClient] = {}

    async def exchange(self, p: Provider, code: str, verifier: str, callback: str) -> str:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                p.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback,
                    "client_id": p.client_id,
                    "client_secret": p.client_secret,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
        if r.status_code != 200:
            raise OidcError(f"el proveedor rechazó el código ({r.status_code})")
        token = r.json().get("id_token")
        if not isinstance(token, str):
            raise OidcError("el proveedor no devolvió id_token")
        return token

    async def key(self, p: Provider, token: str) -> Any:
        client = self._jwks.setdefault(p.name, jwt.PyJWKClient(p.jwks_url, cache_keys=True))
        try:
            # PyJWKClient descarga con urllib (bloqueante): fuera del bucle de eventos
            return (await asyncio.to_thread(client.get_signing_key_from_jwt, token)).key
        except jwt.PyJWTError as e:
            raise OidcError(f"clave de firma desconocida: {e}") from e
