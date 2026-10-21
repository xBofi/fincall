"""Acceso a la consola operativa: Google o Microsoft, solicitudes de acceso y roles.

Decisión de Andreu (2026-10-21): no hay contraseñas. Reglas:
- Se entra con Google o Microsoft (`oidc.py`). La persona es (proveedor, sujeto); el correo solo
  cuenta si el proveedor lo verifica.
- `BC_OWNER_EMAIL` entra directamente como owner. El resto ve una página de bienvenida y solicita
  acceso; el owner lo concede (con un rol) o lo deniega en la pestaña Solicitudes.
- El token de sesión es opaco y aleatorio; en base de datos solo se guarda su hash. Cookie
  HttpOnly, SameSite=Lax, Secure fuera de localhost. Dura `BC_SESION_DIAS` y se alarga sola al
  usarla pasada la mitad: no hay que volver a entrar cada día.
- Gestión de usuarios (`/api/usuarios`) solo para owner. Nunca se puede dejar la consola sin un
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

from building_call.auth import hash_token_sesion
from building_call.domain.operaciones import EstadoAcceso, Rol, Usuario
from building_call.oidc import (
    ClienteOidc,
    ErrorOidc,
    Identidad,
    Intento,
    Proveedor,
    proveedores_configurados,
    url_autorizacion,
    verificar_id_token,
)
from building_call.repos.operativo import RepositorioOperativo, SesionAcceso
from building_call.settings import Settings

logger = logging.getLogger(__name__)

#: Cookie firmada que acompaña la ida y vuelta al proveedor (state, nonce, PKCE)
COOKIE_INTENTO = "bc_oidc"
INTENTO_MAX_S = 600


class CambioUsuario(BaseModel):
    rol: Rol | None = None
    activo: bool | None = None
    nombre: str | None = None


class Concesion(BaseModel):
    rol: Rol = Rol.calidad


class Acceso:
    modo = "oidc"

    def __init__(
        self, repo: RepositorioOperativo, settings: Settings, oidc: ClienteOidc | None = None
    ) -> None:
        self.repo = repo
        self.s = settings
        self.oidc = oidc or ClienteOidc()
        # Sin secreto configurado (desarrollo) la ida y vuelta se firma con uno del proceso
        self._firma = (settings.sesion_secreto or secrets.token_urlsafe(32)).encode()

    def proveedores(self) -> dict[str, Proveedor]:
        return proveedores_configurados(self.s)

    def retorno(self, proveedor: str) -> str:
        return f"{self.s.url_consola.rstrip('/')}/api/auth/oidc/{proveedor}/retorno"

    # ------------------------------------------------------------------ sesiones

    def _segura(self, request: Request) -> bool:
        return request.url.hostname not in ("localhost", "127.0.0.1")

    def _poner_cookie(self, response: Response, request: Request, token: str) -> None:
        response.set_cookie(
            self.s.sesion_cookie,
            token,
            httponly=True,
            samesite="lax",
            secure=self._segura(request),
            max_age=self.s.sesion_dias * 86400,
            path="/",
        )

    async def abrir_sesion(self, usuario: Usuario, response: Response, request: Request) -> str:
        token = secrets.token_urlsafe(32)
        ahora = datetime.now(UTC)
        await self.repo.guardar_sesion(
            SesionAcceso(
                id=f"ses-{uuid4().hex[:16]}",
                usuario_id=usuario.id,
                token_hash=hash_token_sesion(token),
                creada_en=ahora,
                expira_en=ahora + timedelta(days=self.s.sesion_dias),
            )
        )
        self._poner_cookie(response, request, token)
        await self.repo.registrar_auditoria(
            usuario.id, "login", "sesion", {"proveedor": usuario.proveedor}
        )
        return token

    async def persona_de_peticion(
        self, request: Request, response: Response | None = None
    ) -> Usuario | None:
        """Quien tiene sesión, tenga o no acceso concedido. Alarga la sesión si va por la mitad."""
        token = request.cookies.get(self.s.sesion_cookie)
        if not token:
            return None
        token_hash = hash_token_sesion(token)
        sesion = await self.repo.obtener_sesion_por_hash(token_hash)
        ahora = datetime.now(UTC)
        if sesion is None or not sesion.vigente(ahora):
            return None
        usuario = await self.repo.obtener_usuario(sesion.usuario_id)
        if usuario is None or not usuario.activo:
            return None
        duracion = timedelta(days=self.s.sesion_dias)
        if response is not None and sesion.expira_en - ahora < duracion / 2:
            await self.repo.renovar_sesion(token_hash, ahora + duracion)
            self._poner_cookie(response, request, token)
        return usuario

    async def usuario_de_peticion(self, request: Request) -> Usuario | None:
        """Quien puede usar la consola: sesión vigente y acceso concedido."""
        persona = await self.persona_de_peticion(request)
        return persona if persona is not None and persona.puede_entrar() else None

    def requiere(self, *roles: Rol) -> Callable[..., Coroutine[Any, Any, Usuario]]:
        permitidos = set(roles) or {Rol.owner, Rol.administrador, Rol.calidad}

        async def dependencia(request: Request, response: Response) -> Usuario:
            usuario = await self.persona_de_peticion(request, response)
            if usuario is None:
                raise HTTPException(401, "Sesión no válida o expirada")
            if not usuario.puede_entrar():
                raise HTTPException(403, "Acceso pendiente de aprobación")
            if usuario.rol not in permitidos:
                raise HTTPException(403, "Rol sin permiso para este recurso")
            return usuario

        return dependencia

    def requiere_persona(self) -> Callable[..., Coroutine[Any, Any, Usuario]]:
        async def dependencia(request: Request, response: Response) -> Usuario:
            usuario = await self.persona_de_peticion(request, response)
            if usuario is None:
                raise HTTPException(401, "Sesión no válida o expirada")
            return usuario

        return dependencia

    # ------------------------------------------------------------------ ida y vuelta firmada

    def _firmar(self, datos: dict[str, Any]) -> str:
        cuerpo = base64.urlsafe_b64encode(json.dumps(datos).encode()).decode()
        firma = hmac.new(self._firma, cuerpo.encode(), hashlib.sha256).hexdigest()
        return f"{cuerpo}.{firma}"

    def _leer_firmado(self, valor: str | None) -> dict[str, Any] | None:
        if not valor or "." not in valor:
            return None
        cuerpo, firma = valor.rsplit(".", 1)
        esperada = hmac.new(self._firma, cuerpo.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(firma, esperada):
            return None
        datos: dict[str, Any] = json.loads(base64.urlsafe_b64decode(cuerpo))
        if time.time() - float(datos.get("t", 0)) > INTENTO_MAX_S:
            return None
        return datos

    # ------------------------------------------------------------------ alta por proveedor

    async def entrar_con(self, identidad: Identidad) -> Usuario:
        """Crea o actualiza a la persona. El owner configurado entra siempre con acceso."""
        owner = self.s.owner_email.strip().lower()
        es_owner = bool(owner) and identidad.correo_verificado == owner
        existente = await self.repo.obtener_usuario_por_identidad(
            identidad.proveedor, identidad.sujeto
        )
        if existente is None:
            nombre_usuario = identidad.correo or f"{identidad.proveedor}-{identidad.sujeto[:12]}"
            if await self.repo.obtener_usuario_por_nombre(nombre_usuario) is not None:
                # El mismo correo por el otro proveedor es otra identidad hasta que se conceda
                nombre_usuario = f"{nombre_usuario}#{identidad.proveedor}"
            usuario = Usuario(
                id=f"usr-{uuid4().hex[:24]}",
                nombre_usuario=nombre_usuario,
                nombre=identidad.nombre,
                rol=Rol.owner if es_owner else Rol.calidad,
                activo=True,
                email=identidad.correo,
                proveedor=identidad.proveedor,
                sujeto=identidad.sujeto,
                estado_acceso=EstadoAcceso.concedido if es_owner else EstadoAcceso.sin_solicitar,
                creado_en=datetime.now(UTC),
            )
            # Primero el usuario: la auditoría lo referencia (clave foránea en Postgres)
            await self.repo.guardar_usuario(usuario)
            await self.repo.registrar_auditoria(
                usuario.id,
                "alta_por_proveedor",
                "usuario",
                {"proveedor": identidad.proveedor, "email": identidad.correo, "owner": es_owner},
            )
            return usuario
        else:
            cambios: dict[str, Any] = {"email": identidad.correo or existente.email}
            if identidad.nombre:
                cambios["nombre"] = identidad.nombre
            if es_owner:
                cambios.update(rol=Rol.owner, activo=True, estado_acceso=EstadoAcceso.concedido)
            usuario = existente.model_copy(update=cambios)
        await self.repo.guardar_usuario(usuario)
        return usuario

    # ------------------------------------------------------------------ rutas de sesión

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/api/auth")
        persona = self.requiere_persona()

        def _proveedor(nombre: str) -> Proveedor:
            p = self.proveedores().get(nombre)
            if p is None:
                raise HTTPException(404, "Proveedor no configurado")
            return p

        @r.get("/proveedores")
        async def proveedores() -> list[dict[str, str]]:
            return [{"id": p.nombre, "etiqueta": p.etiqueta} for p in self.proveedores().values()]

        @r.get("/estado")
        async def estado(request: Request, response: Response) -> dict[str, Any]:
            usuario = await self.persona_de_peticion(request, response)
            return {
                "modo": self.modo,
                "autenticado": usuario is not None,
                "usuario": usuario.publico() if usuario else None,
            }

        @r.get("/oidc/{nombre}/inicio")
        async def inicio(nombre: str, request: Request) -> RedirectResponse:
            p = _proveedor(nombre)
            intento = Intento.nuevo()
            respuesta = RedirectResponse(
                url_autorizacion(p, intento, self.retorno(nombre)), status_code=302
            )
            respuesta.set_cookie(
                COOKIE_INTENTO,
                self._firmar(
                    {
                        "p": nombre,
                        "state": intento.state,
                        "nonce": intento.nonce,
                        "v": intento.verificador,
                        "t": time.time(),
                    }
                ),
                httponly=True,
                samesite="lax",  # vuelve por una navegación GET desde el proveedor
                secure=self._segura(request),
                max_age=INTENTO_MAX_S,
                path="/api/auth/oidc",
            )
            return respuesta

        @r.get("/oidc/{nombre}/retorno")
        async def retorno(
            nombre: str,
            request: Request,
            code: str | None = None,
            state: str | None = None,
            error: str | None = None,
        ) -> RedirectResponse:
            consola = self.s.url_consola.rstrip("/") + "/"
            fallo = RedirectResponse(consola + "?acceso=error", status_code=302)
            fallo.delete_cookie(COOKIE_INTENTO, path="/api/auth/oidc")
            p = _proveedor(nombre)
            intento = self._leer_firmado(request.cookies.get(COOKIE_INTENTO))
            if (
                error
                or not code
                or not state
                or intento is None
                or intento.get("p") != nombre
                or not hmac.compare_digest(str(intento.get("state")), state)
            ):
                await self.repo.registrar_auditoria(
                    None,
                    "login_rechazado",
                    "sesion",
                    {"proveedor": nombre, "motivo": error or "state"},
                )
                return fallo
            try:
                token = await self.oidc.canjear(p, code, intento["v"], self.retorno(nombre))
                clave = await self.oidc.clave(p, token)
                identidad = verificar_id_token(p, token, clave, intento["nonce"])
            except ErrorOidc as e:
                logger.warning("Inicio de sesión con %s rechazado: %s", nombre, e)
                await self.repo.registrar_auditoria(
                    None, "login_rechazado", "sesion", {"proveedor": nombre, "motivo": str(e)}
                )
                return fallo
            usuario = await self.entrar_con(identidad)
            if not usuario.activo:
                return fallo
            ok = RedirectResponse(consola, status_code=302)
            ok.delete_cookie(COOKIE_INTENTO, path="/api/auth/oidc")
            await self.abrir_sesion(usuario, ok, request)
            return ok

        @r.post("/solicitar")
        async def solicitar(usuario: Usuario = Depends(persona)) -> dict[str, Any]:  # noqa: B008
            if usuario.estado_acceso in (EstadoAcceso.concedido, EstadoAcceso.solicitado):
                return usuario.publico()
            actualizado = usuario.model_copy(
                update={
                    "estado_acceso": EstadoAcceso.solicitado,
                    "solicitado_en": datetime.now(UTC),
                }
            )
            await self.repo.guardar_usuario(actualizado)
            await self.repo.registrar_auditoria(
                usuario.id, "solicitar_acceso", "usuario", {"email": usuario.email}
            )
            return actualizado.publico()

        @r.post("/logout")
        async def logout(request: Request, response: Response) -> dict[str, Any]:
            token = request.cookies.get(self.s.sesion_cookie)
            if token:
                await self.repo.revocar_sesion(hash_token_sesion(token), datetime.now(UTC))
            response.delete_cookie(self.s.sesion_cookie, path="/")
            return {"ok": True}

        @r.get("/me")
        async def me(usuario: Usuario = Depends(self.requiere())) -> dict[str, Any]:  # noqa: B008
            return usuario.publico()

        return r

    # ------------------------------------------------------------------ gestión de usuarios

    def router_usuarios(self) -> APIRouter:
        r = APIRouter(prefix="/api/usuarios")
        owner = self.requiere(Rol.owner)

        async def _cargar(usuario_id: str) -> Usuario:
            objetivo = await self.repo.obtener_usuario(usuario_id)
            if objetivo is None:
                raise HTTPException(404, "Usuario no encontrado")
            return objetivo

        async def _proteger_ultimo_owner(objetivo: Usuario, queda_owner: bool) -> None:
            """Impide dejar la consola sin ningún owner que pueda entrar."""
            if objetivo.rol is not Rol.owner or not objetivo.puede_entrar() or queda_owner:
                return
            otros = [
                u
                for u in await self.repo.listar_usuarios()
                if u.id != objetivo.id and u.rol is Rol.owner and u.puede_entrar()
            ]
            if not otros:
                raise HTTPException(409, "No se puede quitar el último owner activo")

        async def _guardar(
            actor: Usuario, objetivo: Usuario, accion: str, cambios: dict[str, Any]
        ) -> dict[str, Any]:
            actualizado = objetivo.model_copy(update=cambios)
            await self.repo.guardar_usuario(actualizado)
            if not actualizado.puede_entrar():
                await self.repo.revocar_sesiones_de(objetivo.id, datetime.now(UTC))
            await self.repo.registrar_auditoria(
                actor.id,
                accion,
                "usuario",
                {"usuario": objetivo.nombre_usuario, **{k: str(v) for k, v in cambios.items()}},
            )
            return actualizado.publico()

        @r.get("")
        async def listar(actor: Usuario = Depends(owner)) -> list[dict[str, Any]]:  # noqa: B008
            usuarios = await self.repo.listar_usuarios()
            return [u.publico() for u in sorted(usuarios, key=lambda u: u.nombre_usuario)]

        @r.post("/{usuario_id}/conceder")
        async def conceder(
            usuario_id: str,
            cuerpo: Concesion,
            actor: Usuario = Depends(owner),  # noqa: B008
        ) -> dict[str, Any]:
            objetivo = await _cargar(usuario_id)
            if objetivo.id == actor.id:
                raise HTTPException(409, "No puedes cambiar tu propio acceso")
            return await _guardar(
                actor,
                objetivo,
                "conceder_acceso",
                {"estado_acceso": EstadoAcceso.concedido, "rol": cuerpo.rol, "activo": True},
            )

        @r.post("/{usuario_id}/denegar")
        async def denegar(usuario_id: str, actor: Usuario = Depends(owner)) -> dict[str, Any]:  # noqa: B008
            objetivo = await _cargar(usuario_id)
            if objetivo.id == actor.id:
                raise HTTPException(409, "No puedes cambiar tu propio acceso")
            await _proteger_ultimo_owner(objetivo, queda_owner=False)
            return await _guardar(
                actor, objetivo, "denegar_acceso", {"estado_acceso": EstadoAcceso.denegado}
            )

        @r.patch("/{usuario_id}")
        async def modificar(
            usuario_id: str,
            cuerpo: CambioUsuario,
            actor: Usuario = Depends(owner),  # noqa: B008
        ) -> dict[str, Any]:
            objetivo = await _cargar(usuario_id)
            if objetivo.id == actor.id and (cuerpo.rol is not None or cuerpo.activo is not None):
                raise HTTPException(409, "No puedes cambiar tu propio rol ni desactivarte")
            rol_final = cuerpo.rol or objetivo.rol
            activo_final = objetivo.activo if cuerpo.activo is None else cuerpo.activo
            await _proteger_ultimo_owner(objetivo, rol_final is Rol.owner and activo_final)
            return await _guardar(
                actor, objetivo, "modificar_usuario", cuerpo.model_dump(exclude_none=True)
            )

        @r.delete("/{usuario_id}", status_code=204)
        async def eliminar(usuario_id: str, actor: Usuario = Depends(owner)) -> Response:  # noqa: B008
            objetivo = await _cargar(usuario_id)
            if objetivo.id == actor.id:
                raise HTTPException(409, "No puedes eliminar tu propia cuenta")
            await _proteger_ultimo_owner(objetivo, queda_owner=False)
            await self.repo.revocar_sesiones_de(objetivo.id, datetime.now(UTC))
            await self.repo.eliminar_usuario(objetivo.id)
            await self.repo.registrar_auditoria(
                actor.id, "eliminar_usuario", "usuario", {"usuario": objetivo.nombre_usuario}
            )
            return Response(status_code=204)

        return r
