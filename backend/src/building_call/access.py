"""Acceso a la consola operativa: Google o Microsoft, solicitudes de acceso y roles.

Decisión de producto (2026-10-21): no hay contraseñas. Reglas:
- Se entra con Google o Microsoft (`oidc.py`). La persona es (proveedor, sujeto); el correo solo
  cuenta si el proveedor lo verifica.
- `BC_OWNER_EMAIL` entra directamente como owner. El resto ve una página de bienvenida y solicita
  acceso; el owner lo concede (con un rol) o lo deniega en la pestaña Solicitudes.
- El token de sesión es opaco y aleatorio; en base de datos solo se guarda su hash. Cookie
  HttpOnly, SameSite=Lax, Secure fuera de localhost. Dura `BC_SESSION_DAYS` y se alarga sola al
  usarla pasada la mitad: no hay que volver a entrar cada día.
- Gestión de usuarios (`/api/users`) solo para owner. Nunca se puede dejar la consola sin un
  owner activo ni actuar sobre la propia cuenta.
Spec: `docs/superpowers/specs/2026-10-21-console-access-google-microsoft-design.md`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from building_call.audit.access_log import AccessLog, MemoryAccessStore
from building_call.audit.context import set_actor
from building_call.auth import hash_session_token
from building_call.domain.operations import AccessState, Role, User
from building_call.oidc import (
    Attempt,
    Identity,
    OidcClient,
    OidcError,
    Provider,
    authorization_url,
    configured_providers,
    verify_id_token,
)
from building_call.repos.operational import AccessSession, OperationalRepository
from building_call.repos.sql_models import UserSQL
from building_call.settings import Settings

logger = logging.getLogger(__name__)

#: Vida máxima de una sesión aunque se siga usando: luego hay que volver a entrar
SESSION_MAX_DAYS = 90

#: Cookie firmada que acompaña la ida y vuelta al proveedor (state, nonce, PKCE)
ATTEMPT_COOKIE = "bc_oidc"
MAX_ATTEMPT_S = 600


class UserChange(BaseModel):
    role: Role | None = None
    active: bool | None = None
    name: str | None = None


class Grant(BaseModel):
    role: Role = Role.quality


class Access:
    mode = "oidc"

    def __init__(
        self,
        repo: OperationalRepository,
        settings: Settings,
        oidc: OidcClient | None = None,
        access_log: AccessLog | None = None,
    ) -> None:
        self.repo = repo
        self.s = settings
        self.oidc = oidc or OidcClient()
        # Who read or viewed what: it goes with access control, which every route receives
        self.access_log = access_log or AccessLog(MemoryAccessStore())
        # Sin secreto configurado (desarrollo) la ida y vuelta se firma con uno del proceso
        self._signature = (settings.session_secret or secrets.token_urlsafe(32)).encode()

    def providers(self) -> dict[str, Provider]:
        return configured_providers(self.s)

    def callback(self, provider: str) -> str:
        return f"{self.s.console_url.rstrip('/')}/api/auth/oidc/{provider}/callback"

    # ------------------------------------------------------------------ sesiones

    def _secure(self, request: Request) -> bool:
        # Detrás de un túnel o un proxy (Vite con changeOrigin) el Host llega como 127.0.0.1
        # aunque el navegador use https: manda la URL pública de la consola
        if self.s.console_url.startswith("https://"):
            return True
        if request.headers.get("x-forwarded-proto") == "https":
            return True
        return request.url.hostname not in ("localhost", "127.0.0.1")

    def _set_cookie(self, response: Response, request: Request, token: str) -> None:
        response.set_cookie(
            self.s.session_cookie,
            token,
            httponly=True,
            samesite="lax",
            secure=self._secure(request),
            max_age=self.s.session_days * 86400,
            path="/",
        )

    async def open_session(self, user: User, response: Response, request: Request) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        await self.repo.save_session(
            AccessSession(
                id=f"ses-{uuid4().hex[:16]}",
                user_id=user.id,
                token_hash=hash_session_token(token),
                created_at=now,
                expires_at=now + timedelta(days=self.s.session_days),
            )
        )
        self._set_cookie(response, request, token)
        await self.repo.record_audit(
            user.id, "login", "session", {"provider": user.provider}
        )
        return token

    async def person_from_request(
        self, request: Request, response: Response | None = None
    ) -> User | None:
        """Quien tiene sesión, tenga o no acceso concedido. Alarga la sesión si va por la mitad."""
        token = request.cookies.get(self.s.session_cookie)
        if not token:
            return None
        token_hash = hash_session_token(token)
        session = await self.repo.get_session_by_hash(token_hash)
        now = datetime.now(UTC)
        if session is None or not session.is_current(now):
            return None
        if now - session.created_at > timedelta(days=SESSION_MAX_DAYS):
            # La renovación deslizante tiene techo: una cookie robada no vale para siempre
            return None
        user = await self.repo.get_user(session.user_id)
        if user is None or not user.active:
            return None
        set_actor(f"user:{user.id}", "console")
        duration = timedelta(days=self.s.session_days)
        if response is not None and session.expires_at - now < duration / 2:
            await self.repo.renew_session(token_hash, now + duration)
            self._set_cookie(response, request, token)
        return user

    async def user_from_request(self, request: Request) -> User | None:
        """Quien puede usar la consola: sesión vigente y acceso concedido."""
        person = await self.person_from_request(request)
        return person if person is not None and person.can_sign_in() else None

    def requires(self, *roles: Role) -> Callable[..., Coroutine[Any, Any, User]]:
        allowed = set(roles) or {Role.owner, Role.administrator, Role.quality}

        async def dependency(request: Request, response: Response) -> User:
            user = await self.person_from_request(request, response)
            if user is None:
                raise HTTPException(401, "Sesión no válida o expirada")
            if not user.can_sign_in():
                raise HTTPException(403, "Acceso pendiente de aprobación")
            if user.role not in allowed:
                raise HTTPException(403, "Rol sin permiso para este recurso")
            return user

        return dependency

    def requires_person(self) -> Callable[..., Coroutine[Any, Any, User]]:
        async def dependency(request: Request, response: Response) -> User:
            user = await self.person_from_request(request, response)
            if user is None:
                raise HTTPException(401, "Sesión no válida o expirada")
            return user

        return dependency

    # ------------------------------------------------------------------ ida y vuelta firmada

    def _sign(self, data: dict[str, Any]) -> str:
        body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
        signature = hmac.new(self._signature, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def _read_signed(self, value: str | None) -> dict[str, Any] | None:
        if not value or "." not in value:
            return None
        body, signature = value.rsplit(".", 1)
        expected_sig = hmac.new(self._signature, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body))
        if time.time() - float(data.get("t", 0)) > MAX_ATTEMPT_S:
            return None
        return data

    # ------------------------------------------------------------------ alta por proveedor

    async def sign_in_with(self, identity: Identity) -> User:
        """Crea o actualiza a la persona. El owner configurado entra siempre con acceso."""
        owner = self.s.owner_email.strip().lower()
        is_owner = bool(owner) and identity.verified_email == owner
        existing = await self.repo.get_user_by_identity(
            identity.provider, identity.subject
        )
        if existing is None:
            # Solo el correo que el proveedor verifica identifica a alguien: en un inquilino propio
            # de Microsoft cualquiera se pone el de otra persona (revisión de seguridad 2026-10-22)
            email = identity.verified_email
            username = email or f"{identity.provider}-{identity.subject[:12]}"
            if await self.repo.get_user_by_name(username) is not None:
                # El mismo correo por el otro proveedor es otra identidad hasta que se conceda
                username = f"{username}#{identity.provider}"
            user = User(
                id=f"usr-{uuid4().hex[:24]}",
                username=username,
                name=identity.name,
                role=Role.owner if is_owner else Role.quality,
                active=True,
                email=email,
                provider=identity.provider,
                subject=identity.subject,
                access_state=AccessState.granted if is_owner else AccessState.not_requested,
                created_at=datetime.now(UTC),
            )
            # Signs themself up when coming in with their provider
            set_actor(f"user:{user.id}", "console")
            # Primero el usuario: la auditoría lo referencia (clave foránea en Postgres)
            await self.repo.save_user(user)
            await self.repo.record_audit(
                user.id,
                "provider_signup",
                "user",
                {
                    "provider": identity.provider,
                    "email": identity.verified_email,
                    "owner": is_owner,
                },
            )
            return user
        else:
            changes: dict[str, Any] = {"email": identity.verified_email or existing.email}
            if identity.name:
                changes["name"] = identity.name
            if is_owner:
                changes.update(role=Role.owner, active=True, access_state=AccessState.granted)
            user = existing.model_copy(update=changes)
            set_actor(f"user:{user.id}", "console")
        await self.repo.save_user(user)
        return user

    # ------------------------------------------------------------------ rutas de sesión

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/api/auth")
        person = self.requires_person()

        def _provider(name: str) -> Provider:
            p = self.providers().get(name)
            if p is None:
                raise HTTPException(404, "Proveedor no configurado")
            return p

        @r.get("/providers")
        async def providers() -> list[dict[str, str]]:
            return [{"id": p.name, "label": p.label} for p in self.providers().values()]

        @r.get("/state")
        async def state(request: Request, response: Response) -> dict[str, Any]:
            user = await self.person_from_request(request, response)
            return {
                "mode": self.mode,
                "authenticated": user is not None,
                "user": user.public() if user else None,
            }

        @r.get("/oidc/{name}/start")
        async def start(name: str, request: Request) -> RedirectResponse:
            p = _provider(name)
            attempt = Attempt.fresh()
            reply = RedirectResponse(
                authorization_url(p, attempt, self.callback(name)), status_code=302
            )
            reply.set_cookie(
                ATTEMPT_COOKIE,
                self._sign(
                    {
                        "p": name,
                        "state": attempt.state,
                        "nonce": attempt.nonce,
                        "v": attempt.verifier,
                        "t": time.time(),
                    }
                ),
                httponly=True,
                samesite="lax",  # vuelve por una navegación GET desde el proveedor
                secure=self._secure(request),
                max_age=MAX_ATTEMPT_S,
                path="/api/auth/oidc",
            )
            return reply

        @r.get("/oidc/{name}/callback")
        async def callback(
            name: str,
            request: Request,
            code: str | None = None,
            state: str | None = None,
            error: str | None = None,
        ) -> RedirectResponse:
            console = self.s.console_url.rstrip("/") + "/"
            failure = RedirectResponse(console + "?access=error", status_code=302)
            failure.delete_cookie(ATTEMPT_COOKIE, path="/api/auth/oidc")
            p = _provider(name)
            attempt = self._read_signed(request.cookies.get(ATTEMPT_COOKIE))
            if (
                error
                or not code
                or not state
                or attempt is None
                or attempt.get("p") != name
                or not hmac.compare_digest(str(attempt.get("state")), state)
            ):
                await self.repo.record_audit(
                    None,
                    "login_rechazado",
                    "session",
                    {"provider": name, "reason": error or "state"},
                )
                return failure
            try:
                token = await self.oidc.exchange(p, code, attempt["v"], self.callback(name))
                key = await self.oidc.key(p, token)
                identity = verify_id_token(p, token, key, attempt["nonce"])
            except OidcError as e:
                logger.warning("Inicio de sesión con %s rechazado: %s", name, e)
                await self.repo.record_audit(
                    None, "login_rechazado", "session", {"provider": name, "reason": str(e)}
                )
                return failure
            user = await self.sign_in_with(identity)
            if not user.active:
                return failure
            ok = RedirectResponse(console, status_code=302)
            ok.delete_cookie(ATTEMPT_COOKIE, path="/api/auth/oidc")
            await self.open_session(user, ok, request)
            return ok

        @r.post("/submit_request")
        async def submit_request(user: User = Depends(person)) -> dict[str, Any]:  # noqa: B008
            if user.access_state in (AccessState.granted, AccessState.requested):
                return user.public()
            if (
                user.access_state is AccessState.denied
                and user.requested_at
                and datetime.now(UTC) - user.requested_at < timedelta(days=1)
            ):
                raise HTTPException(429, "Ya lo solicitaste hoy; podrás volver a pedirlo mañana")
            updated = user.model_copy(
                update={
                    "access_state": AccessState.requested,
                    "requested_at": datetime.now(UTC),
                }
            )
            await self.repo.save_user(updated)
            await self.repo.record_audit(
                user.id, "request_access", "user", {"email": user.email}
            )
            return updated.public()

        @r.post("/logout")
        async def logout(request: Request, response: Response) -> dict[str, Any]:
            token = request.cookies.get(self.s.session_cookie)
            if token:
                await self.repo.revoke_session(hash_session_token(token), datetime.now(UTC))
            response.delete_cookie(self.s.session_cookie, path="/")
            return {"ok": True}

        @r.get("/me")
        async def me(user: User = Depends(self.requires())) -> dict[str, Any]:  # noqa: B008
            return user.public()

        return r

    # ------------------------------------------------------------------ gestión de usuarios

    def users_router(self) -> APIRouter:
        r = APIRouter(prefix="/api/users")
        owner = self.requires(Role.owner)

        async def _load(user_id: str) -> User:
            target = await self.repo.get_user(user_id)
            if target is None:
                raise HTTPException(404, "Usuario no encontrado")
            return target

        async def _protect_last_owner(target: User, owner_remains: bool) -> None:
            """Impide dejar la consola sin ningún owner que pueda entrar."""
            if target.role is not Role.owner or not target.can_sign_in() or owner_remains:
                return
            others = [
                u
                for u in await self.repo.list_users()
                if u.id != target.id and u.role is Role.owner and u.can_sign_in()
            ]
            if not others:
                raise HTTPException(409, "No se puede quitar el último owner activo")

        async def _save(
            actor: User, target: User, action: str, changes: dict[str, Any]
        ) -> dict[str, Any]:
            updated = target.model_copy(update=changes)
            await self.repo.save_user(updated)
            if not updated.can_sign_in():
                await self.repo.revoke_sessions_of(target.id, datetime.now(UTC))
            await self.repo.record_audit(
                actor.id,
                action,
                "user",
                {"user": target.username, **{k: str(v) for k, v in changes.items()}},
            )
            return updated.public()

        @r.get("")
        async def list_all(actor: User = Depends(owner)) -> list[dict[str, Any]]:  # noqa: B008
            users = await self.repo.list_users()
            await self.access_log.list(UserSQL, {}, rows=len(users))
            return [u.public() for u in sorted(users, key=lambda u: u.username)]

        @r.post("/{user_id}/grant")
        async def grant(
            user_id: str,
            body: Grant,
            actor: User = Depends(owner),  # noqa: B008
        ) -> dict[str, Any]:
            target = await _load(user_id)
            if target.id == actor.id:
                raise HTTPException(409, "No puedes cambiar tu propio acceso")
            return await _save(
                actor,
                target,
                "grant_access",
                {"access_state": AccessState.granted, "role": body.role, "active": True},
            )

        @r.post("/{user_id}/deny")
        async def deny(user_id: str, actor: User = Depends(owner)) -> dict[str, Any]:  # noqa: B008
            target = await _load(user_id)
            if target.id == actor.id:
                raise HTTPException(409, "No puedes cambiar tu propio acceso")
            await _protect_last_owner(target, owner_remains=False)
            return await _save(
                actor, target, "deny_access", {"access_state": AccessState.denied}
            )

        @r.patch("/{user_id}")
        async def modify(
            user_id: str,
            body: UserChange,
            actor: User = Depends(owner),  # noqa: B008
        ) -> dict[str, Any]:
            target = await _load(user_id)
            if target.id == actor.id and (body.role is not None or body.active is not None):
                raise HTTPException(409, "No puedes cambiar tu propio rol ni desactivarte")
            final_role = body.role or target.role
            final_active = target.active if body.active is None else body.active
            await _protect_last_owner(target, final_role is Role.owner and final_active)
            return await _save(
                actor, target, "modify_user", body.model_dump(exclude_none=True)
            )

        @r.delete("/{user_id}", status_code=204)
        async def remove(user_id: str, actor: User = Depends(owner)) -> Response:  # noqa: B008
            target = await _load(user_id)
            if target.id == actor.id:
                raise HTTPException(409, "No puedes eliminar tu propia cuenta")
            await _protect_last_owner(target, owner_remains=False)
            await self.repo.revoke_sessions_of(target.id, datetime.now(UTC))
            await self.repo.delete_user(target.id)
            await self.repo.record_audit(
                actor.id, "delete_user", "user", {"user": target.username}
            )
            return Response(status_code=204)

        return r
