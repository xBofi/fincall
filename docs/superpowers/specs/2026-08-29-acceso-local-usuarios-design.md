# Acceso local a la consola operativa (sustituye a Google OIDC)

**Fecha:** 2026-08-29 · **Estado:** implementado en `develop`.

## Por qué

La consola es para personal interno. Google OAuth exigía una pantalla de consentimiento de tipo
*Externo* al no existir Google Workspace, es decir, una app "para el público" con verificación y
usuarios de prueba: lo contrario de lo que se quiere. Decisión de Andreu: quitar Google y gestionar
los usuarios dentro de la propia aplicación, con una pestaña para añadir y eliminar personas.

## Diseño

- **Identidad**: `usuarios(nombre_usuario único, nombre, rol, activo, hash_contrasena,
  debe_cambiar_contrasena, creado_en)`. Sin correo ni identidad externa. Migración `0003` vacía las
  filas Google previas (no tenían valor) y cambia las FK: `sesiones` → `ON DELETE CASCADE`,
  `auditoria_eventos.usuario_id` → `ON DELETE SET NULL` (la baja conserva el rastro sin autor).
- **Contraseñas**: `hashlib.scrypt` (n=2^14, r=8, p=1, sal de 16 bytes), sin dependencias externas.
  Verificación en tiempo constante; ante usuario inexistente se verifica un hash señuelo para no
  revelar existencia por tiempo. Política: ≥10 caracteres, distinta del nombre de usuario y de una
  lista corta de contraseñas obvias. Las temporales (`secrets.token_urlsafe(12)`) la eluden porque
  son de un solo uso.
- **Arranque**: `Acceso.asegurar_admin_inicial()` crea `BC_ADMIN_INICIAL_USUARIO`/`_CONTRASENA`
  (por defecto `admin`/`admin`) solo si no hay ningún usuario, con `debe_cambiar_contrasena=True`.
  Con cambio pendiente la única API accesible es `/api/auth/*` (403 en el resto); el frontend abre un
  modal no cerrable hasta cambiarla.
- **Sesiones**: igual que antes (token opaco aleatorio, solo hash en BD, cookie `HttpOnly`,
  `SameSite=Lax`, `Secure` fuera de localhost, 12 h). Se revocan todas las del usuario al cambiar la
  contraseña, restablecerla, desactivarlo o eliminarlo.
- **Fuerza bruta**: `LimitadorIntentos` en memoria, ventana deslizante; 5 fallos por usuario o por IP
  → 429 durante 15 min (`BC_LOGIN_MAX_INTENTOS`, `BC_LOGIN_BLOQUEO_S`). Suficiente para una
  instancia interna; si hubiera varias réplicas habría que moverlo a PostgreSQL.
- **Gestión** (`/api/usuarios`, solo `owner`): listar, alta (devuelve la temporal UNA vez), `PATCH`
  rol/activo/nombre, restablecer (nueva temporal), eliminar. Invariantes: nadie edita su propio rol
  ni se desactiva/elimina a sí mismo; nunca se puede dejar la consola sin un owner activo (409).
  Toda acción queda en `auditoria_eventos` (`crear_usuario`, `modificar_usuario`,
  `restablecer_contrasena`, `eliminar_usuario`, `login`, `login_rechazado`, `login_bloqueado`,
  `cambio_contrasena`, `admin_inicial_creado`).
- **Se elimina**: Authlib, `itsdangerous`, `SessionMiddleware`, `BC_AUTH_MODO=desarrollo`
  (el admin inicial cubre el desarrollo local sin puertas traseras), allowlists `BC_ACCESO_*`.

## Frontend

Un solo `App.tsx`: sin sesión se muestra `Login`; con sesión, el workbench con tres pantallas
(`Segmented` en la cabecera): **Operaciones** (indicadores, cola, dossier, llamada en directo),
**Latencia** (los paneles `PanelLatencia` y `PanelEstado` de la antigua consola de validación,
que estaban muertos en un bloque comentado) y **Usuarios** (solo owner, `GestionUsuarios`).
Menú de usuario: cambiar contraseña, salir. Todo `fetch` pasa por `pedir()`, que lanza `ApiError`
con el `detail` de FastAPI para mostrar mensajes útiles.

## Fuera de alcance / pendiente

- Los endpoints de la llamada (`/api/llamadas*`, WebSocket de voz) siguen sin autenticación: los
  usará la pasarela telefónica. Cuando exista, protegerlos con una clave de servicio o red interna.
- Recuperación de contraseña sin owner: no hay correo; si se pierde el único owner, se restablece
  con SQL sobre `usuarios` (o vaciando la tabla para que renazca el admin inicial).
- 2FA/TOTP: no ahora; la superficie es interna y la política de bloqueo cubre el riesgo actual.
