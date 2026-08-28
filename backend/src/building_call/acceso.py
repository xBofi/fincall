"""Acceso a la consola operativa: Google OIDC (Authlib), sesión por cookie y roles.

Reglas (handoff de Codex, 2026-08-28):
- Sin secreto de sesión o sin allowlist no se habilita el acceso.
- El token de sesión es opaco y aleatorio; en base de datos solo se guarda su hash.
- Cookie HttpOnly, SameSite=Lax, Secure fuera de localhost.
- `BC_AUTH_MODO=desarrollo` crea un owner local sin Google SOLO para desarrollo (aviso en /salud).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from building_call.auth import hash_token_sesion, usuario_desde_google
from building_call.domain.operaciones import Rol, Usuario
from building_call.repos.operativo import RepositorioOperativo, SesionAcceso
from building_call.settings import Settings


def _lista(valor: str) -> set[str]:
    return {v.strip().lower() for v in valor.split(",") if v.strip()}


class Acceso:
    def __init__(self, repo: RepositorioOperativo, settings: Settings) -> None:
        self.repo = repo
        self.s = settings
        self.owners = _lista(settings.acceso_owners)
        self.administradores = _lista(settings.acceso_administradores)
        self.calidad = _lista(settings.acceso_calidad)
        self.permitidos = self.owners | self.administradores | self.calidad
        self._oauth: Any = None

    # ------------------------------------------------------------------ estado

    @property
    def modo(self) -> str:
        if self.s.auth_modo == "desarrollo":
            return "desarrollo"
        if self.habilitado_google:
            return "google"
        return "deshabilitado"

    @property
    def habilitado_google(self) -> bool:
        return bool(
            self.s.google_client_id
            and self.s.google_client_secret
            and self.s.sesion_secreto
            and self.permitidos
        )

    def _cliente_google(self) -> Any:
        if self._oauth is None:
            from authlib.integrations.starlette_client import OAuth

            self._oauth = OAuth()
            self._oauth.register(
                name="google",
                client_id=self.s.google_client_id,
                client_secret=self.s.google_client_secret,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        return self._oauth.google

    # ------------------------------------------------------------------ sesiones

    async def abrir_sesion(self, usuario: Usuario, response: Response, request: Request) -> str:
        token = secrets.token_urlsafe(32)
        ahora = datetime.now(UTC)
        await self.repo.guardar_usuario(usuario)
        await self.repo.guardar_sesion(
            SesionAcceso(
                id=f"ses-{uuid4().hex[:16]}",
                usuario_id=usuario.id,
                token_hash=hash_token_sesion(token),
                creada_en=ahora,
                expira_en=ahora + timedelta(hours=self.s.sesion_horas),
            )
        )
        response.set_cookie(
            self.s.sesion_cookie,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.hostname not in ("localhost", "127.0.0.1"),
            max_age=self.s.sesion_horas * 3600,
            path="/",
        )
        await self.repo.registrar_auditoria(usuario.id, "login", "sesion", {"rol": usuario.rol})
        return token

    async def usuario_de_peticion(self, request: Request) -> Usuario | None:
        if self.modo == "desarrollo":
            usuario_dev = Usuario(
                id="usr-desarrollo",
                google_sub="desarrollo",
                email=self.s.auth_dev_email,
                rol=Rol.owner,
                activo=True,
            )
            if not getattr(self, "_dev_persistido", False):
                # La auditoría referencia usuarios por clave ajena: el owner local debe existir
                await self.repo.guardar_usuario(usuario_dev)
                self._dev_persistido = True
            return usuario_dev
        token = request.cookies.get(self.s.sesion_cookie)
        if not token:
            return None
        sesion = await self.repo.obtener_sesion_por_hash(hash_token_sesion(token))
        if sesion is None or not sesion.vigente(datetime.now(UTC)):
            return None
        usuario = await self.repo.obtener_usuario(sesion.usuario_id)
        if usuario is None or not usuario.activo:
            return None
        return usuario

    def requiere(self, *roles: Rol) -> Callable[[Request], Coroutine[Any, Any, Usuario]]:
        permitidos = set(roles) or {Rol.owner, Rol.administrador, Rol.calidad}

        async def dependencia(request: Request) -> Usuario:
            usuario = await self.usuario_de_peticion(request)
            if usuario is None:
                raise HTTPException(401, "Sesión no válida o expirada")
            if usuario.rol not in permitidos:
                raise HTTPException(403, "Rol sin permiso para este recurso")
            return usuario

        return dependencia

    # ------------------------------------------------------------------ rutas

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/api/auth")

        @r.get("/estado")
        async def estado(request: Request) -> dict[str, Any]:
            usuario = await self.usuario_de_peticion(request)
            return {
                "modo": self.modo,
                "autenticado": usuario is not None,
                "usuario": usuario.model_dump() if usuario else None,
            }

        @r.get("/login")
        async def login(request: Request) -> Any:
            if not self.habilitado_google:
                raise HTTPException(
                    503,
                    "Acceso no habilitado: faltan BC_GOOGLE_CLIENT_ID/SECRET, BC_SESION_SECRETO "
                    "o la allowlist BC_ACCESO_*",
                )
            return await self._cliente_google().authorize_redirect(
                request, self.s.google_redirect_url
            )

        @r.get("/callback")
        async def callback(request: Request) -> Any:
            if not self.habilitado_google:
                raise HTTPException(503, "Acceso no habilitado")
            token = await self._cliente_google().authorize_access_token(request)
            claims = token.get("userinfo") or {}
            try:
                usuario = usuario_desde_google(
                    dict(claims),
                    permitidos=self.permitidos,
                    owners=self.owners,
                    administradores=self.administradores,
                    calidad=self.calidad,
                )
            except PermissionError as e:
                await self.repo.registrar_auditoria(
                    None,
                    "login_rechazado",
                    "sesion",
                    {"motivo": str(e), "email": claims.get("email")},
                )
                raise HTTPException(403, str(e)) from e
            respuesta = RedirectResponse(self.s.frontend_url, status_code=303)
            await self.abrir_sesion(usuario, respuesta, request)
            return respuesta

        @r.post("/logout")
        async def logout(request: Request, response: Response) -> dict[str, Any]:
            token = request.cookies.get(self.s.sesion_cookie)
            if token:
                await self.repo.revocar_sesion(hash_token_sesion(token), datetime.now(UTC))
            response.delete_cookie(self.s.sesion_cookie, path="/")
            return {"ok": True}

        @r.get("/me")
        async def me(usuario: Usuario = Depends(self.requiere())) -> dict[str, Any]:  # noqa: B008
            return usuario.model_dump()

        return r
