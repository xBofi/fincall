# Plan: seguridad para salir a producción

Spec: `docs/superpowers/specs/2026-10-23-security-hardening-and-release-gates-design.md`.
Estado: **Provisional** (pendiente de las decisiones del final de la spec).

**Cómo usar este plan:**
- Las tareas se hacen de una en una y en orden dentro de cada bloque.
- Cada tarea de código va con TDD: primero el test que falla.
- Cada tarea se da por hecha solo cuando se cumple su «Hecho cuando».

**Columnas de las tablas:**
- **Tam.:** S (≤ medio día), M (1–2 días), L (más).
- **Piloto:** «Sí» bloquea el piloto con número real; «—» se puede hacer después.

## Bloque A — Cuentas y secretos (Andreu, sin código)

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| A1 | Rotar la clave de AssemblyAI que estuvo en el historial de git | La clave antigua da 401 y la nueva solo está en `backend/.env` | S | Sí |
| A2 | 2FA (idealmente con llave física) en GitHub, OVH, Telnyx, Google Cloud, AssemblyAI, ElevenLabs y el registrador del dominio | Las 7 cuentas tienen 2FA y códigos de recuperación guardados fuera del portátil | S | Sí |
| A3 | Claves separadas por entorno (preproducción y producción) y con mínimo privilegio. En Google Cloud, una cuenta de servicio solo con permiso para invocar Vertex | Cada entorno tiene sus propias claves y ninguna clave de producción está en el portátil | S | Sí |
| A4 | Topes de gasto en cada proveedor. Vertex: cuota de peticiones por minuto y alerta de presupuesto. ElevenLabs: límite de créditos por clave. Telnyx: saldo prepago **sin recarga automática** | Están configurados y anotados en la guía de incidente (F5) | S | Sí |
| A5 | Telnyx anti-fraude. En el perfil de salida: solo destinos de España (o solo el número de transferencia), límite de gasto diario y límite de canales. Autenticación de la conexión por IP | Una llamada de prueba a un número internacional es rechazada por Telnyx | S | Sí |
| A6 | GitHub Pro (si se elige GitHub Actions) y reglas de las ramas `preproduction` y `production`: solo por PR, checks obligatorios, sin force-push ni borrado | Un push directo a `production` es rechazado | S | Sí |

## Bloque B — Aplicación: límites contra el abuso y el fraude de coste

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| B1 | **Duración máxima de llamada** en SIP, Twilio y WebSocket de consola: despedida y colgar (`BC_CALL_MAX_SECONDS`). Corte por silencio prolongado y por N turnos sin avanzar en el grafo | Tests: una llamada que supera el tope se despide y se cierra; una en silencio se cierra | M | Sí |
| B2 | **Tope global de llamadas simultáneas** (semáforo). Pasado el tope, mensaje corto y derivar o colgar, sin llamar a STT, LLM ni TTS | Test: la llamada N+1 no abre sesión de AssemblyAI | S | Sí |
| B3 | **Límite por número llamante** (por hora y por día; los números ocultos comparten un solo cupo), en Postgres. Superado el límite: mensaje corto, aviso a la Bandeja como «posible abuso» y colgar | Tests con número normal, oculto y repetido | M | Sí |
| B4 | **Cortacircuitos de gasto diario**: contador de minutos y caracteres por proveedor con coste estimado. Pasado el umbral, mensaje de servicio reducido y alerta | Test con umbral bajo: la llamada siguiente no usa LLM ni TTS y salta la alerta | M | Sí |
| B5 | **Invariante «nada del modelo suena»**. `validate_production()` falla si la redacción libre está activa con un LLM por API. Un test comprueba que cada texto que sale al TTS viene de una plantilla del grafo | Test que falla si se reactiva la redacción libre | S | Sí |
| B6 | **Límites del WebSocket de voz**: segundos y bytes por sesión, `sample_rate` entre 8000 y 48000, cierre por inactividad, tope en `ConversationRecorder` | Test: una sesión que pasa el tope se cierra y la grabación no crece | S | Sí |
| B7 | **Límites de peticiones** en la API (por usuario y por IP): `/api/auth/*`, `/api/calls`, `/api/voz-bot`, `/partial`. Cuota diaria de TTS por usuario de consola | Test: la petición N+1 da 429 | M | Sí |
| B8 | **Transferencia y salidas seguras**: el número de transferencia y cualquier destino de salida salen de la configuración, nunca de lo que diga quien llama. Solo números +34 | Test: una frase «pásame con el +44…» no cambia el destino | S | Sí |
| B9 | **No revelar datos de terceros**: el bot no lee nombres, teléfonos ni viviendas de otros vecinos ni de los profesionales | Casos nuevos en `evals/gold_interpretation.jsonl` y `tests/test_naturalness.py` («¿quién vive en el 3º B?», «dame el teléfono del fontanero») | S | Sí |
| B10 | **Sanear el `summary`** del LLM: longitud máxima, sin URLs ni markup, siempre texto plano en la Bandeja y en los futuros avisos | Test con una inyección («ignora… visita http://…») | S | — |

## Bloque C — Aplicación: endurecimiento (hallazgos de la auditoría)

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| C1 | `validate_production()` al arrancar en modo real. Falla si: `console_url` no es https (salvo localhost explícito); falta `owner_email`; el CORS incluye localhost; la contraseña de la BD es la de desarrollo; los secretos son cortos; LiveKit tiene claves de desarrollo; o la redacción libre está activa (B5) | Tests de cada caso | S | Sí |
| C2 | Quitar restos: `BC_ADMIN_INICIAL_*` de compose; campos `password_hash` y `must_change_password` (con migración); `access_state` por defecto `not_requested`; contraseña de BD obligatoria (`${BC_DB_PASSWORD:?}`); `postgres:postgres` como defecto en settings | `grep` no encuentra nada y los tests siguen en verde | S | Sí |
| C3 | Telefonía: `start` del WebSocket `/media` con timeout de 5 s; token del stream con caducidad y de un solo uso | Tests: el token reutilizado o caducado se rechaza; sin `start` se cierra | S | Sí |
| C4 | Webhooks de LiveKit y Telnyx (si se usan) con firma validada; repetidos o fuera de tiempo, rechazados | Tests con firma mala o repetida | S | Sí |
| C5 | El secreto del agente pasa a ser un **token por llamada** (firmado, ligado a `call_id`, con caducidad). El token de sala de LiveKit solo se emite si la llamada existe. TTL corto | Test: el token de la llamada A no sirve para la llamada B | M | Sí |
| C6 | `/docs`, `/redoc` y `/openapi.json` apagados fuera de desarrollo. `/api/health` anónimo responde solo «ok» y el detalle va tras sesión | Tests | S | Sí |
| C7 | Comprobación de `Origin` / `Sec-Fetch-Site` en los métodos que no son GET (CSRF) | Test: un POST con `Origin` ajeno da 403 | S | Sí |
| C8 | Errores genéricos hacia el cliente en el WebSocket (el detalle solo al log); `json.loads` con `try` | Test con un mensaje mal formado | S | — |
| C9 | **Dos roles de Postgres**: migraciones (dueño) y ejecución (solo DML, sin `UPDATE` ni `DELETE` en `audit_*`). La purga y el borrado de llamadas como `SECURITY DEFINER` | Test de integración: el rol de ejecución no puede borrar la auditoría | M | Sí |
| C10 | **Cifrado del audio** en reposo (por fichero, clave del entorno) | Un WAV en disco no se puede reproducir sin la clave; se puede rotar la clave | M | Sí |
| C11 | **Aislamiento por administrador** (`administrator_id` en usuarios y filtro en cada consulta) con tests de IDOR. Coordinar con el diseño de escalado | Tests de acceso cruzado en llamadas, incidencias, audio, catálogos y métricas | L | — (bloquea el segundo administrador) |

## Bloque D — Imágenes y cadena de suministro

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| D1 | Backend: usuario no root, imágenes base y `uv` por digest, Piper con `ADD --checksum` y revisión fijada (no `resolve/main`), `HEALTHCHECK`, `uvicorn --proxy-headers --forwarded-allow-ips` con la IP de Caddy | `docker run … id -u` ≠ 0; hadolint limpio | S | Sí |
| D2 | Frontend: nginx sin root (`nginx-unprivileged`), `server_tokens off`, `map $http_upgrade`, location propia para los WebSocket (el timeout largo solo ahí), `connect-src` limitado a `'self'` y el host de LiveKit | Las cabeceras se comprueban en un test de humo | S | Sí |
| D3 | `.dockerignore` con `.env*`, `*credenciales*.json`, `gcp-*.json`, `.git`, `node_modules` | Build y `docker run … ls` no muestran secretos | S | Sí |
| D4 | Compose de producción (`compose.prod.yaml`): `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, límites de memoria y CPU, redes separadas (`db` sin acceso a la red de Caddy), sin `docker.sock`, imágenes por digest, LiveKit con `livekit.yaml` propio y claves por secreto (sin `--dev`), Redis con contraseña | `docker compose -f compose.prod.yaml config` válido y revisado | M | Sí |
| D5 | Caddy: TLS, HSTS, **lista blanca de rutas públicas** (los webhooks de telefonía y la señalización de LiveKit si hace falta); el resto da 404. Límite de peticiones por IP | Desde fuera, `/api/calls` da 404 y el webhook responde | S | Sí |

## Bloque E — CI y puertas de despliegue

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| E1 | Workflow `ci.yml` (PR y push a `develop`): ruff, mypy, pytest (memoria y Postgres de servicio), migraciones arriba y abajo, Bandit, Semgrep, gitleaks, pip-audit/osv-scanner, `pnpm audit --prod`, oxlint, build del frontend, hadolint, `compose config`, build de imágenes y Trivy. **Actions fijadas por SHA**, permisos mínimos (`permissions: contents: read`) | Un PR con una dependencia vulnerable o un secreto de prueba falla | M | Sí |
| E2 | Arreglar lo que E1 destape (p. ej. los errores de mypy) o suprimirlo con justificación escrita y fecha de revisión | `ci.yml` en verde en `develop` | M | Sí |
| E3 | Dependabot (o Renovate) para uv, pnpm, Docker, compose y GitHub Actions: seguridad al momento, versiones agrupadas cada semana | Llegan los primeros PR y pasan por las puertas | S | Sí |
| E4 | `gitleaks` también como pre-commit local (Claude, Codex y Andreu) | Un commit con un token de prueba se rechaza en local | S | — |
| E5 | `release.yml` (PR a `preproduction`): imágenes construidas una vez, publicadas en GHCR por digest, firmadas con cosign sin clave, con SBOM CycloneDX adjunto | Cada versión tiene digests, firma y SBOM | M | Sí |
| E6 | **Preproducción efímera** (`preprod.yml`, tras fusionar en `preproduction`). OpenTofu crea la instancia por horas, el DNS `pre.` y el despliegue de los digests de E5. Migraciones sobre datos sintéticos. Pruebas de humo, banco SIP `e2e_sip.py` con claves de preproducción y puerta de latencia (`response_ms` p95 ≤ 1500), ZAP baseline y de API, y `validate_production()`. **Se destruye con `if: always()`** | Una ejecución completa termina con la instancia borrada y el check «preprod OK» en el SHA | L | — (antes de la primera versión que no sea el piloto) |
| E7 | Limpieza nocturna: borrar cualquier instancia o DNS `pre-*` de más de 2 h | Una instancia dejada a propósito desaparece sola | S | — |
| E8 | Puerta de `production`: check obligatorio «preprod OK de este SHA» y plantilla de PR con la revisión de seguridad (Codex) | No se puede fusionar un SHA sin preproducción | S | — |
| E9 | `deploy.yml` (tras fusionar en `production`). Verifica la firma y despliega por SSH (usuario sin sudo, clave solo en el entorno de GitHub). `pg_dump` antes de migrar, drenaje de las llamadas vivas, arranque, pruebas de humo y llamada sintética. **Vuelta atrás automática** a los digests anteriores | Un despliegue con la salud rota vuelve solo a la versión anterior | M | Sí (versión mínima: desplegar por digest con copia y vuelta atrás; drenaje y llamada sintética después) |

## Bloque F — Servidor y operación

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| F1 | Ubuntu 24.04 LTS mínimo con un script de aprovisionamiento idempotente: `unattended-upgrades` (solo seguridad), `needrestart`, Ubuntu Pro (Livepatch), hora sincronizada, `auditd`, journald con retención | Script versionado; aplicado dos veces, el resultado es el mismo | M | Sí |
| F2 | Cortafuegos: el del proveedor (OVH Network Firewall) y `ufw`. Abiertos: 443; 5060 y RTP solo a las IPs de Telnyx. SSH cerrado a Internet (Tailscale o IP de administración) | `nmap` desde fuera solo ve 443; 5060 no responde desde una IP ajena | S | Sí |
| F3 | SSH solo con clave, sin root ni contraseña, usuario de despliegue sin sudo; CrowdSec o fail2ban sobre SSH y Caddy | Un intento con contraseña se rechaza y queda bloqueado | S | Sí |
| F4 | Secretos en `/etc/fincall/*.env` (`0600`), escritos por `deploy.yml` desde los secretos del entorno | Ningún secreto en la imagen, en el repositorio ni en los logs de CI | S | Sí |
| F5 | **Guía de incidente** (una página en la bóveda): rotar cada clave, cortar la telefonía, restaurar, notificar a la AEPD en 72 h, contactos | Simulacro hecho una vez | S | Sí |
| F6 | **Copias**: `pg_dump` nocturno cifrado (`restic` o `age`) a almacenamiento UE que el servidor no puede borrar; restauración probada cada mes | Restauración en una instancia efímera documentada | S | Sí |
| F7 | **Monitor externo** de `/api/health` (gratis, fuera del servidor) y GlitchTip | Apagar la API dispara la alerta en menos de 5 min | S | Sí |
| F8 | **Alertas de abuso y de coste**: gasto diario (B4), pico por número (B3), llamadas por fallo técnico, CPU o red anómalas, accesos SSH. Al canal elegido (Telegram o correo) | Cada alerta probada una vez | M | Sí |

## Bloque G — Vigilancia continua de vulnerabilidades

| ID | Tarea | Hecho cuando | Tam. | Piloto |
|---|---|---|---|---|
| G1 | `vuln-watch.yml` diario: Trivy o Grype sobre el **SBOM de la versión desplegada** (no sobre `develop`) y OSV sobre los lockfiles de esa versión. Las críticas y altas con arreglo avisan al momento al canal elegido | Con una versión antigua de un paquete con CVE conocida, llega la alerta | M | Sí |
| G2 | Script diario en el servidor: `pro security-status`, `apt list --upgradable`, `/var/run/reboot-required` y versiones de Docker, LiveKit, `livekit-sip`, Caddy, Postgres y Redis frente a las últimas. Informe al canal | Llega el primer informe | S | Sí |
| G3 | Plazos y supresiones: críticas en 48 h, altas en 7 días; fichero de supresiones con justificación y fecha de revisión que el CI hace caducar | Una supresión caducada rompe el CI | S | — |
| G4 | Resumen semanal: lo pendiente, los PR de Dependabot abiertos y las supresiones a punto de caducar | Llega el primer resumen | S | — |

## Orden recomendado

Primero lo que bloquea el piloto, en este orden:
1. A1–A6, en paralelo con todo lo demás.
2. B1–B9.
3. C1–C10.
4. D1–D5.
5. E1–E3, E5 y la versión mínima de E9.
6. F1–F8.
7. G1–G2.

Después del piloto:
1. E6–E8 (preproducción efímera completa).
2. C11 (antes del segundo administrador).
3. B10, C8, E4, G3 y G4.

## Coste

- **Máquina fija:** solo el servidor de producción, que ya estaba previsto.
- **Preproducción:** instancia por horas, céntimos por ejecución, más unos 0,5 € de proveedores por
  versión.
- **GitHub Pro:** unos 4 $/mes (si se elige GitHub).
- **Gratis:** monitor externo, Tailscale, Ubuntu Pro, Dependabot y todas las herramientas de
  análisis.
