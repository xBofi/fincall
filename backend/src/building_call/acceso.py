"""Acceso a la consola operativa: usuarios internos con contraseña, sesión por cookie y roles.

La consola es privada: no hay proveedor externo de identidad. Reglas:
- Contraseñas solo como hash scrypt (`auth.py`); el token de sesión es opaco y aleatorio y en
  base de datos solo se guarda su hash. Cookie HttpOnly, SameSite=Lax, Secure fuera de localhost.
- Al arrancar, si no existe ningún usuario se crea el administrador inicial
  (`BC_ADMIN_INICIAL_USUARIO` / `BC_ADMIN_INICIAL_CONTRASENA`) con cambio de contraseña
  obligatorio: hasta cambiarla solo puede usar `/api/auth/*`.
- Bloqueo temporal tras varios intentos fallidos por usuario y por IP (en memoria del proceso).
- Gestión de usuarios (`/api/usuarios`) solo para `owner`: alta con contraseña temporal,
  cambio de rol, activar/desactivar, restablecer contraseña y baja. Nunca se puede dejar la
  consola sin un owner activo ni actuar sobre la propia cuenta.
"""

from __future__ import annotations

import logging
import secrets
from collections import defaultdict, deque
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from building_call.auth import (
    generar_contrasena_temporal,
    hash_contrasena,
    hash_token_sesion,
    validar_contrasena_nueva,
    validar_nombre_usuario,
    verificar_contrasena,
)
from building_call.domain.operaciones import Rol, Usuario
from building_call.repos.operativo import RepositorioOperativo, SesionAcceso
from building_call.settings import Settings

logger = logging.getLogger(__name__)

# Contraseñas por defecto conocidas: solo válidas para el primer arranque local
_CONTRASENAS_DEFECTO = {"admin"}


class Credenciales(BaseModel):
    usuario: str
    contrasena: str


class CambioContrasena(BaseModel):
    actual: str
    nueva: str


class AltaUsuario(BaseModel):
    nombre_usuario: str
    rol: Rol
    nombre: str | None = None
    contrasena: str | None = None  # si no se indica, se genera una temporal


class CambioUsuario(BaseModel):
    rol: Rol | None = None
    activo: bool | None = None
    nombre: str | None = None


class LimitadorIntentos:
    """Ventana deslizante de fallos por clave (usuario o IP). Sin persistencia: basta para
    frenar fuerza bruta en un servicio interno de una sola instancia."""

    def __init__(self, maximo: int, ventana_s: int) -> None:
        self.maximo = maximo
        self.ventana = timedelta(seconds=ventana_s)
        self._fallos: dict[str, deque[datetime]] = defaultdict(deque)

    def _purgar(self, clave: str, ahora: datetime) -> deque[datetime]:
        cola = self._fallos[clave]
        while cola and ahora - cola[0] > self.ventana:
            cola.popleft()
        return cola

    def bloqueado(self, clave: str, ahora: datetime | None = None) -> bool:
        return len(self._purgar(clave, ahora or datetime.now(UTC))) >= self.maximo

    def fallo(self, clave: str, ahora: datetime | None = None) -> None:
        ahora = ahora or datetime.now(UTC)
        self._purgar(clave, ahora).append(ahora)

    def limpiar(self, clave: str) -> None:
        self._fallos.pop(clave, None)


class Acceso:
    modo = "local"

    def __init__(self, repo: RepositorioOperativo, settings: Settings) -> None:
        self.repo = repo
        self.s = settings
        self.limitador = LimitadorIntentos(settings.login_max_intentos, settings.login_bloqueo_s)

    # ------------------------------------------------------------------ arranque

    async def asegurar_admin_inicial(self) -> Usuario | None:
        """Crea el owner inicial si la consola no tiene ningún usuario. Idempotente."""
        if await self.repo.listar_usuarios():
            return None
        nombre = validar_nombre_usuario(self.s.admin_inicial_usuario)
        usuario = Usuario(
            id=f"usr-{uuid4().hex[:24]}",
            nombre_usuario=nombre,
            nombre="Administrador inicial",
            rol=Rol.owner,
            activo=True,
            hash_contrasena=hash_contrasena(self.s.admin_inicial_contrasena),
            debe_cambiar_contrasena=True,
            creado_en=datetime.now(UTC),
        )
        await self.repo.guardar_usuario(usuario)
        await self.repo.registrar_auditoria(
            None, "admin_inicial_creado", "usuario", {"usuario": nombre}
        )
        if self.s.admin_inicial_contrasena in _CONTRASENAS_DEFECTO:
            logger.warning(
                "Owner inicial '%s' creado con la contraseña por defecto: cámbiala en el primer acceso",
                nombre,
            )
        return usuario

    # ------------------------------------------------------------------ sesiones

    async def abrir_sesion(self, usuario: Usuario, response: Response, request: Request) -> str:
        token = secrets.token_urlsafe(32)
        ahora = datetime.now(UTC)
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

    def requiere(
        self, *roles: Rol, permitir_cambio_pendiente: bool = False
    ) -> Callable[[Request], Coroutine[Any, Any, Usuario]]:
        permitidos = set(roles) or {Rol.owner, Rol.administrador, Rol.calidad}

        async def dependencia(request: Request) -> Usuario:
            usuario = await self.usuario_de_peticion(request)
            if usuario is None:
                raise HTTPException(401, "Sesión no válida o expirada")
            if usuario.debe_cambiar_contrasena and not permitir_cambio_pendiente:
                raise HTTPException(403, "Debe cambiar la contraseña antes de continuar")
            if usuario.rol not in permitidos:
                raise HTTPException(403, "Rol sin permiso para este recurso")
            return usuario

        return dependencia

    # ------------------------------------------------------------------ login

    async def autenticar(self, nombre: str, contrasena: str, ip: str) -> Usuario:
        try:
            nombre = validar_nombre_usuario(nombre)
        except ValueError as e:
            raise HTTPException(401, "Usuario o contraseña incorrectos") from e
        clave_usuario, clave_ip = f"u:{nombre}", f"ip:{ip}"
        if self.limitador.bloqueado(clave_usuario) or self.limitador.bloqueado(clave_ip):
            await self.repo.registrar_auditoria(
                None, "login_bloqueado", "sesion", {"usuario": nombre}
            )
            raise HTTPException(429, "Demasiados intentos fallidos; espera unos minutos")
        usuario = await self.repo.obtener_usuario_por_nombre(nombre)
        # Se verifica siempre un hash para no revelar por tiempo si el usuario existe
        hash_objetivo = usuario.hash_contrasena if usuario else _HASH_SENUELO
        valido = verificar_contrasena(contrasena, hash_objetivo) and usuario is not None
        if not valido or usuario is None or not usuario.activo:
            self.limitador.fallo(clave_usuario)
            self.limitador.fallo(clave_ip)
            await self.repo.registrar_auditoria(
                None, "login_rechazado", "sesion", {"usuario": nombre}
            )
            raise HTTPException(401, "Usuario o contraseña incorrectos")
        self.limitador.limpiar(clave_usuario)
        return usuario

    # ------------------------------------------------------------------ rutas de sesión

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/api/auth")
        con_cambio_pendiente = self.requiere(permitir_cambio_pendiente=True)

        @r.get("/estado")
        async def estado(request: Request) -> dict[str, Any]:
            usuario = await self.usuario_de_peticion(request)
            return {
                "modo": self.modo,
                "autenticado": usuario is not None,
                "usuario": usuario.publico() if usuario else None,
            }

        @r.post("/login")
        async def login(
            cuerpo: Credenciales, request: Request, response: Response
        ) -> dict[str, Any]:
            ip = request.client.host if request.client else "desconocida"
            usuario = await self.autenticar(cuerpo.usuario, cuerpo.contrasena, ip)
            await self.abrir_sesion(usuario, response, request)
            return usuario.publico()

        @r.post("/logout")
        async def logout(request: Request, response: Response) -> dict[str, Any]:
            token = request.cookies.get(self.s.sesion_cookie)
            if token:
                await self.repo.revocar_sesion(hash_token_sesion(token), datetime.now(UTC))
            response.delete_cookie(self.s.sesion_cookie, path="/")
            return {"ok": True}

        @r.get("/me")
        async def me(usuario: Usuario = Depends(con_cambio_pendiente)) -> dict[str, Any]:
            return usuario.publico()

        @r.post("/contrasena")
        async def cambiar_contrasena(
            cuerpo: CambioContrasena,
            request: Request,
            response: Response,
            usuario: Usuario = Depends(con_cambio_pendiente),
        ) -> dict[str, Any]:
            if not verificar_contrasena(cuerpo.actual, usuario.hash_contrasena):
                raise HTTPException(400, "La contraseña actual no es correcta")
            try:
                validar_contrasena_nueva(cuerpo.nueva, usuario.nombre_usuario)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            actualizado = usuario.model_copy(
                update={
                    "hash_contrasena": hash_contrasena(cuerpo.nueva),
                    "debe_cambiar_contrasena": False,
                }
            )
            await self.repo.guardar_usuario(actualizado)
            # Cierra el resto de sesiones (posible contraseña temporal filtrada) y abre una nueva
            await self.repo.revocar_sesiones_de(usuario.id, datetime.now(UTC))
            await self.abrir_sesion(actualizado, response, request)
            await self.repo.registrar_auditoria(usuario.id, "cambio_contrasena", "usuario", {})
            return actualizado.publico()

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

        async def _proteger_ultimo_owner(objetivo: Usuario, cambio: CambioUsuario | None) -> None:
            """Impide dejar la consola sin ningún owner activo."""
            if objetivo.rol is not Rol.owner or not objetivo.activo:
                return
            if cambio is not None:
                rol_final = cambio.rol or objetivo.rol
                activo_final = objetivo.activo if cambio.activo is None else cambio.activo
                if rol_final is Rol.owner and activo_final:
                    return
            otros = [
                u
                for u in await self.repo.listar_usuarios()
                if u.id != objetivo.id and u.rol is Rol.owner and u.activo
            ]
            if not otros:
                raise HTTPException(409, "No se puede quitar el último owner activo")

        @r.get("")
        async def listar(actor: Usuario = Depends(owner)) -> list[dict[str, Any]]:
            usuarios = await self.repo.listar_usuarios()
            return [u.publico() for u in sorted(usuarios, key=lambda u: u.nombre_usuario)]

        @r.post("", status_code=201)
        async def crear(cuerpo: AltaUsuario, actor: Usuario = Depends(owner)) -> dict[str, Any]:
            try:
                nombre = validar_nombre_usuario(cuerpo.nombre_usuario)
                temporal = cuerpo.contrasena is None
                contrasena = cuerpo.contrasena or generar_contrasena_temporal()
                if not temporal:
                    validar_contrasena_nueva(contrasena, nombre)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            if await self.repo.obtener_usuario_por_nombre(nombre) is not None:
                raise HTTPException(409, "Ya existe un usuario con ese nombre")
            nuevo = Usuario(
                id=f"usr-{uuid4().hex[:24]}",
                nombre_usuario=nombre,
                nombre=cuerpo.nombre,
                rol=cuerpo.rol,
                activo=True,
                hash_contrasena=hash_contrasena(contrasena),
                debe_cambiar_contrasena=True,
                creado_en=datetime.now(UTC),
            )
            await self.repo.guardar_usuario(nuevo)
            await self.repo.registrar_auditoria(
                actor.id, "crear_usuario", "usuario", {"usuario": nombre, "rol": cuerpo.rol}
            )
            # La contraseña temporal se devuelve UNA vez, para entregarla en mano al usuario
            return {**nuevo.publico(), "contrasena_temporal": contrasena if temporal else None}

        @r.patch("/{usuario_id}")
        async def modificar(
            usuario_id: str, cuerpo: CambioUsuario, actor: Usuario = Depends(owner)
        ) -> dict[str, Any]:
            objetivo = await _cargar(usuario_id)
            if objetivo.id == actor.id and (cuerpo.rol is not None or cuerpo.activo is not None):
                raise HTTPException(409, "No puedes cambiar tu propio rol ni desactivarte")
            await _proteger_ultimo_owner(objetivo, cuerpo)
            cambios = cuerpo.model_dump(exclude_none=True)
            actualizado = objetivo.model_copy(update=cambios)
            await self.repo.guardar_usuario(actualizado)
            if cambios.get("activo") is False:
                await self.repo.revocar_sesiones_de(objetivo.id, datetime.now(UTC))
            await self.repo.registrar_auditoria(
                actor.id,
                "modificar_usuario",
                "usuario",
                {"usuario": objetivo.nombre_usuario, **cambios},
            )
            return actualizado.publico()

        @r.post("/{usuario_id}/restablecer")
        async def restablecer(usuario_id: str, actor: Usuario = Depends(owner)) -> dict[str, Any]:
            objetivo = await _cargar(usuario_id)
            temporal = generar_contrasena_temporal()
            actualizado = objetivo.model_copy(
                update={
                    "hash_contrasena": hash_contrasena(temporal),
                    "debe_cambiar_contrasena": True,
                }
            )
            await self.repo.guardar_usuario(actualizado)
            await self.repo.revocar_sesiones_de(objetivo.id, datetime.now(UTC))
            await self.repo.registrar_auditoria(
                actor.id, "restablecer_contrasena", "usuario", {"usuario": objetivo.nombre_usuario}
            )
            return {**actualizado.publico(), "contrasena_temporal": temporal}

        @r.delete("/{usuario_id}", status_code=204)
        async def eliminar(usuario_id: str, actor: Usuario = Depends(owner)) -> Response:
            objetivo = await _cargar(usuario_id)
            if objetivo.id == actor.id:
                raise HTTPException(409, "No puedes eliminar tu propia cuenta")
            await _proteger_ultimo_owner(objetivo, None)
            await self.repo.revocar_sesiones_de(objetivo.id, datetime.now(UTC))
            await self.repo.eliminar_usuario(objetivo.id)
            await self.repo.registrar_auditoria(
                actor.id, "eliminar_usuario", "usuario", {"usuario": objetivo.nombre_usuario}
            )
            return Response(status_code=204)

        return r


# Hash de un secreto aleatorio: iguala el coste de verificar usuarios inexistentes
_HASH_SENUELO = hash_contrasena(secrets.token_urlsafe(16))
