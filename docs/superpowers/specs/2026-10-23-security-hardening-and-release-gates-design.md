# Seguridad para salir a producción: servidor, telefonía, dependencias y puertas de despliegue

Fecha: 2026-10-23 · Estado: **Provisional** (propuesta pendiente de revisión) ·
Tareas: `docs/superpowers/plans/2026-10-23-security-hardening.md`.

Parte de lo que ya hay: la revisión de seguridad del 2026-10-22 (nota de la bóveda
«Seguridad por diseño»), el diseño SIP con Telnyx (`2026-10-22-telnyx-sip-pilot-design.md`), el
plan de escalado (`2026-10-10-plan-de-escalado-design.md`) y una auditoría del código en `develop`
(`f8a0a5a`, 2026-10-23). Los precios de terceros que aparecen aquí son orientativos: hay que
comprobarlos al contratar.

## Objetivo

1. Que el servidor público sea difícil de atacar y que, si algo cae, el daño quede acotado.
2. Que nadie pueda usar nuestra telefonía ni nuestra IA a nuestra costa.
3. Enterarnos el mismo día si una dependencia, una imagen o el propio Ubuntu pasan a tener una
   vulnerabilidad conocida.
4. Que a producción solo llegue código que ha pasado pruebas y análisis en una preproducción
   igual a producción, **sin pagar una máquina fija para preproducción**.

## Modelo de amenazas

### Qué protegemos

| Activo | Por qué importa |
|---|---|
| Saldo y cuotas de Telnyx, Vertex, AssemblyAI y ElevenLabs | Es dinero: el fraude se factura a nosotros |
| Voz, transcripciones y datos de vecinos y fincas | RGPD: datos personales, y la voz es especialmente sensible |
| Claves de proveedores, secreto de sesión y del agente, claves de LiveKit | Con ellas se hace todo lo anterior sin pasar por nuestro código |
| Consola (owner y administradores) | Ve todo y edita fincas y profesionales |
| Auditoría | Es la prueba de lo que pasó; un atacante intentaría borrarla |
| Disponibilidad del número | Si el bot no contesta, el vecino no puede avisar de una avería |

### Superficie expuesta en producción

- **Telefonía:** SIP 5060 y el rango RTP de `livekit-sip`. Solo debe ser alcanzable desde Telnyx.
- **HTTPS 443 (Caddy):** webhooks de los proveedores y, si hace falta, la señalización `wss` de
  LiveKit. Nada más.
- **Administración:** SSH. No debe estar abierto a Internet.
- **Consola:** en el piloto, **no expuesta**: se entra por túnel (SSH o Tailscale), como dice el
  diseño SIP.
- **Cadena de suministro:** paquetes PyPI y npm, imágenes base, GitHub Actions, modelos
  descargados.
- **Cuentas de terceros:** GitHub, OVH (hosting), Telnyx, Google Cloud, AssemblyAI,
  ElevenLabs y el registrador del dominio.

## Estudio: tráfico malicioso contra un voicebot

El riesgo existe y tiene nombre. Esta es la lista completa de lo que conocemos,
ordenada por lo probable que es en nuestro caso.

### 1. Fraude de peaje por la centralita (toll fraud / IRSF)

**Qué es.** El ataque más caro y el más habitual contra cualquier centralita SIP. Unos bots
escanean Internet buscando puertos 5060 abiertos (SIPVicious, «friendly-scanner») y prueban
contraseñas. Si consiguen colarse por un trunk de salida, llaman a números de tarificación
especial internacionales que son suyos (IRSF, International Revenue Share Fraud), normalmente de
noche o en fin de semana. En unas horas se van miles de euros.

**Cómo nos afecta.**
- Tenemos un trunk de salida para la transferencia a una persona.
- El banco de pruebas usa otro trunk de salida (`fincall-banco-salida`).
- La fase 0 autentica con usuario y contraseña.
- Con las claves de LiveKit se puede pedir una llamada saliente (`CreateSIPParticipant`).

**Controles.**
- Cortafuegos: 5060 y RTP **solo** hacia las IPs de Telnyx.
- En producción, autenticación por IP y sin contraseña SIP.
- Ningún trunk de pruebas en producción.
- En Telnyx, el perfil de salida admite solo destinos españoles (idealmente solo el número de
  transferencia). Lleva límite de gasto diario y de canales, y saldo prepago sin recarga
  automática.
- El número de transferencia sale de la configuración y nunca de lo que diga quien llama.

### 2. Usar nuestra IA gratis (AI freeloading / LLM-jacking)

Puede ocurrir de cuatro formas:

- **(a) Por la propia llamada.** Alguien llama e intenta que el bot le haga de ChatGPT: «olvida
  tus instrucciones y escríbeme…». Aquí la arquitectura nos protege mucho. El LLM solo rellena
  claves con valores validados contra enums. Lo que suena **siempre sale de plantillas del
  grafo**, y con un LLM por API la redacción libre está desactivada. Ningún texto del modelo llega
  al oído del que llama, así que no hay chatbot que robar. Hay que **fijarlo con un test y con una
  comprobación al arrancar** para que nadie lo rompa sin querer.
- **(b) Por nuestras puertas web.** Un WebSocket de voz o un endpoint de TTS sin sesión da STT,
  LLM y TTS gratis. Los WebSocket ya están cerrados: el de telefonía se cerró el 2026-10-22 y el de
  la consola exige sesión. Faltan los límites de duración, de volumen y de cuota.
- **(c) Robando nuestras claves.** Es lo más caro y está muy extendido en 2025–2026: claves de nube
  robadas para usar modelos ajenos. Controles:
  - claves de mínimo privilegio, distintas por entorno;
  - cuotas por minuto en Vertex y alertas de presupuesto (una alerta no corta el gasto por sí
    sola: hace falta la cuota o un corte automático);
  - límite de créditos por clave donde el proveedor lo permita;
  - rotación de claves;
  - secretos fuera de la imagen y de Git.
- **(d) Por LiveKit.** Con claves débiles o de desarrollo (`devkey/secret`), cualquiera crea salas y
  despacha nuestro agente. Un servidor TURN con credenciales fijas sirve además de relé de tráfico
  gratis. Controles:
  - claves propias largas (las de desarrollo ya se rechazan fuera de localhost);
  - tokens de sala cortos y ligados a una llamada que existe;
  - TURN con credenciales temporales o desactivado si no hace falta;
  - el puerto de la API de LiveKit no se expone.

### 3. Bombeo de tráfico y saturación de la línea (traffic pumping / TDoS)

**Qué es.** Llamadas automáticas, largas o masivas al número: robocalls, música en espera,
silencio, alguien que nos quiere hacer daño o un error de desvío en bucle. Cada minuto cuesta
dinero (Telnyx, AssemblyAI, ElevenLabs y el LLM) y ocupa canales que necesitan los vecinos
reales. Hoy no hay duración máxima, ni tope de llamadas a la vez, ni límite por número (hallazgo
**alto** de la auditoría).

**Controles.**
- Duración máxima por llamada.
- Corte por silencio o por falta de progreso (N turnos sin avanzar en el grafo).
- Tope global de llamadas simultáneas y tope por número llamante (por hora y por día).
- Política para los números ocultos.
- Un **cortacircuitos de gasto diario**: pasado un umbral, el bot da un mensaje corto y deriva o
  cuelga, y nos avisa.
- Límite de canales también en Telnyx.

### 4. Escaneo y abuso del puerto SIP

**Qué es.** INVITE flood, enumeración de extensiones, REGISTER a fuerza bruta y paquetes
malformados contra `livekit-sip`.

**Controles.**
- Allowlist de IPs en el cortafuegos del proveedor, que lo corta antes de llegar al servidor.
- `allowed_addresses` y `allowed_numbers` en el trunk de entrada.
- Imagen de `livekit-sip` fijada y al día.

### 5. Suplantación del número llamante e ingeniería social

**Qué es.** El caller ID se falsifica sin esfuerzo. Alguien puede hacerse pasar por un vecino o por
el administrador para:
- sacar datos de otros vecinos («¿quién vive en el 3º B?», «¿qué teléfono tiene el
  fontanero?»);
- crear incidencias falsas que el sistema intente atender, lo que costará dinero cuando haya avisos
  reales.

**Controles.**
- El número nunca da privilegios. Ya es regla Confirmada de la bóveda: «el teléfono del llamante
  nunca es el único método de identificación».
- El bot no lee en voz alta datos de terceros (test).
- Los avisos pasan por el administrador mientras sigan «pendientes de envío».
- Límite de incidencias por número y señal de «posible abuso» en la Bandeja.

### 6. Inyección de instrucciones por voz

**Qué es.** Si el modelo no puede actuar, una inyección solo puede ensuciar datos. Lo que queda
expuesto es el `summary` libre, que llega a la Bandeja y, en el futuro, a los profesionales.

**Controles.**
- Recortar y sanear el resumen.
- Mostrarlo como texto plano. React ya escapa y no se usa `dangerouslySetInnerHTML`.
- Si un día el resumen sale por correo o SMS, sanearlo para ese canal y que siga pasando por una
  persona.

### 7. Llamada perdida para que devolvamos la llamada (wangiri) y devolución de llamadas

**Qué es.** Si un día el bot devuelve llamadas o manda SMS, un atacante deja llamadas perdidas
desde números de tarificación especial para que paguemos nosotros.

**Controles.**
- Nunca llamar ni enviar SMS a un número que dicte quien llama sin validarlo.
- Solo números españoles.
- Límite diario de salidas.

### 8. Tráfico web malicioso

**Qué es.**
- Escáneres de vulnerabilidades y bots.
- Fuerza bruta: aquí no hay contraseñas, pero sí el arranque de OIDC.
- Inundación L7.
- Webhooks falsificados o repetidos.

**Controles.**
- Caddy solo deja pasar una **lista blanca de rutas**: los webhooks y nada más. Con la consola por
  túnel, Internet no ve ni la consola ni `/api/*`.
- Límites de peticiones por IP.
- CrowdSec o fail2ban sobre los logs de Caddy y SSH.
- Firma de cada webhook (Twilio ya la tiene; los de LiveKit y Telnyx también deben validarse) y
  tokens de un solo uso con caducidad.

### 9. Compromiso del servidor

**Qué es.** Por una dependencia vulnerable, un fallo de la aplicación o una clave SSH. Lo típico
después:
- minar criptomonedas;
- robar el `.env` (lo que lleva a los puntos 1 y 2);
- borrar la auditoría;
- cifrar la base de datos para pedir rescate.

**Controles.**
- Contenedores sin root y de solo lectura, sin capacidades y sin `docker.sock`.
- Rol de Postgres de ejecución sin permisos sobre la auditoría.
- Copias cifradas fuera del servidor que el servidor no puede borrar.
- Alertas de CPU y de salida de red anómalas.

### 10. Cadena de suministro

**Qué es.**
- Un paquete PyPI o npm comprometido o con *typosquatting*.
- Una GitHub Action secuestrada, como `tj-actions/changed-files` en 2025.
- Un modelo descargado sin checksum (el Dockerfile baja Piper de `resolve/main`).

**Controles.**
- Lockfiles congelados (ya los hay).
- Actions fijadas por SHA.
- Checksums en cada descarga.
- Escaneo continuo.
- Imágenes firmadas y desplegadas por digest.

## Diseño

### Servidor (un VPS en la UE)

- **Sistema:** Ubuntu 24.04 LTS mínimo.
  - `unattended-upgrades` aplica solo los parches de seguridad, con reinicio programado de
    madrugada si hace falta (`needrestart`).
  - Ubuntu Pro gratuito (hasta 5 máquinas personales) para Livepatch y ESM.
- **Cortafuegos del proveedor** como primera barrera (en OVH, el *Network Firewall* gratuito de la IP; tiene un número limitado de reglas, comprobar que caben las IPs de Telnyx):
  - 443 abierto;
  - 5060 y RTP solo a las IPs de Telnyx;
  - SSH cerrado a Internet: solo por Tailscale o desde una IP de administración.

  `ufw` queda como segunda barrera. Ojo: **Docker se salta `ufw`** al publicar puertos, así que todo
  contenedor publica en `127.0.0.1` (ya es así en compose) salvo Caddy y `livekit-sip`.
- **SSH:**
  - solo con clave (ed25519, idealmente con llave física);
  - sin root y sin contraseña;
  - usuario sin sudo para desplegar;
  - CrowdSec o fail2ban.
- **Docker:**
  - usuario no root en cada imagen;
  - `read_only`, `cap_drop: [ALL]` y `no-new-privileges`;
  - límites de memoria y CPU;
  - redes separadas (la BD no comparte red con Caddy);
  - nunca `docker.sock` en un contenedor;
  - imágenes por digest.
- **Secretos:**
  - `/etc/fincall/*.env` con permisos `0600` del usuario de despliegue;
  - salen de los secretos del entorno de CI y no entran nunca en la imagen ni en Git;
  - claves distintas en preproducción y en producción.
- **Postgres:**
  - dos roles: migraciones (dueño) y ejecución (solo DML, sin `UPDATE` ni `DELETE` en
    `audit_*`);
  - la purga y el borrado de llamadas como funciones `SECURITY DEFINER`;
  - contraseña generada y no expuesto.
- **Datos:**
  - audio cifrado (por fichero con una clave del entorno, o un volumen cifrado);
  - copia nocturna cifrada (`restic` o `age`) a almacenamiento UE con bloqueo de borrado (o
    credenciales de solo escritura);
  - una restauración probada al mes.
- **Registros:** sin secretos y sin números completos (ya es regla), con retención acotada.
  `auditd` para los accesos de administración.

### Aplicación

Es lo que falta según la auditoría. Las tareas están en el plan.

- **Límites de coste por llamada y globales:**
  - duración y bytes por sesión;
  - llamadas simultáneas;
  - límite por número y por usuario de consola;
  - cortacircuitos de gasto.
- **Aislamiento por administrador** (IDOR): bloquea entrar en varias fincas, no el piloto con un
  solo administrador.
- **Endurecimiento de rutas:**
  - `start` del WebSocket con timeout;
  - token de `media` con caducidad y de un solo uso;
  - token del agente por llamada;
  - `/docs` apagado;
  - `/api/health` mínimo;
  - comprobación de `Origin` en los métodos que no son GET.
- **`validate_production()` al arrancar:** hace fallar el arranque si hay CORS con localhost,
  claves por defecto, redacción libre activada con LLM por API, `console_url` sin https, etc.
- **Imagen:**
  - sin root;
  - digest fijado;
  - `ADD --checksum`;
  - `HEALTHCHECK`;
  - `--proxy-headers`;
  - `.dockerignore` con `.env*` y `*credenciales*`.
- **Restos que hay que quitar:** las variables `BC_ADMIN_INICIAL_*`, los campos de contraseña, la
  contraseña de BD por defecto y LiveKit `--dev` fuera de local.

### CI/CD y puertas

**Plataforma (decisión de producto).** El repositorio está en GitHub y ya tiene las ramas `develop`,
`preproduction` y `production`, pero CLAUDE.md fija GitLab CI para la app.

La recomendación es **GitHub Actions**: el código ya vive allí, no hay que migrar nada y Dependabot
sale gratis. El matiz es de coste: en un repositorio **privado** de una cuenta gratuita, las reglas
de protección de ramas no se aplican. Hace falta **GitHub Pro** (unos 4 $/mes, comprobar), y con él
vienen más minutos de Actions.

Lo que de pago no compensa:
- el análisis de secretos y CodeQL de GitHub en privado;
- en su lugar, `gitleaks` y Semgrep CE más Bandit, gratis.

**Puertas.** Todo corre en los runners de GitHub, sin máquina propia. Un runner autoalojado en el
servidor de producción queda descartado: es una puerta trasera.

| Momento | Qué se comprueba | Si falla |
|---|---|---|
| PR y push a `develop` (minutos, sin coste) | ruff, mypy, pytest (memoria y Postgres de servicio), test de migraciones arriba y abajo, Bandit, Semgrep, gitleaks, pip-audit/osv-scanner sobre `uv.lock`, `pnpm audit --prod`, oxlint y build del frontend, hadolint, `docker compose config`, build de imágenes y Trivy (falla con HIGH/CRITICAL que tengan arreglo) | No se puede fusionar |
| PR `develop → preproduction` | Lo anterior, más construir las imágenes **una sola vez**, subirlas a GHCR por digest, firmarlas (cosign sin clave) y generar el SBOM | No se puede fusionar |
| Tras fusionar en `preproduction` | **Preproducción efímera** (abajo): despliegue de esos digests, migraciones sobre datos sintéticos, pruebas de humo, banco de llamadas SIP con proveedores reales, puerta de latencia (`response_ms` p95 ≤ 1500), ZAP baseline y de API, y comprobación de la configuración de producción. Al acabar se destruye | `production` no acepta la fusión de ese commit |
| PR `preproduction → production` | Check obligatorio: «la preproducción de este SHA pasó». Revisión de seguridad (Codex) en la descripción del PR | No se puede fusionar |
| Tras fusionar en `production` | Despliegue de **los mismos digests**: verificar la firma, copia de la BD antes de migrar, migraciones compatibles hacia atrás, drenar las llamadas vivas, arrancar, pruebas de humo y llamada sintética, y **vuelta atrás automática** a los digests anteriores si falla la salud | Se vuelve a la versión anterior y se avisa |

**Preproducción efímera.** No necesita máquina fija:
- el workflow crea una instancia **facturada por horas** con OpenTofu, a partir de la misma
  imagen de sistema y el mismo compose que producción. En OVH, los VPS se pagan por meses, así
  que la preproducción va en una instancia de **Public Cloud** (por horas, API OpenStack);
- apunta un subdominio `pre.` por la API de DNS y despliega;
- la prueba completa dura unos 20–40 min;
- `if: always()` lo destruye al acabar, aunque falle;
- un trabajo programado cada noche borra cualquier VPS `pre-*` que haya quedado vivo, para
  que un fallo de CI no cueste dinero.

Coste orientativo por versión: el VPS unos céntimos; unas 10 llamadas sintéticas con proveedores
reales, unos 0,5 € (con claves de preproducción con límite propio).

Se prueba SIP contra `livekit-sip` con el banco `e2e_sip.py`, sin operador: no hace falta un
segundo número de Telnyx. La llamada real por Telnyx se hace a mano antes de cada cambio de
telefonía.

**Sí hace falta una máquina fija: el servidor de producción.** Es la del piloto, que ya estaba
prevista. Nada más.

### Vigilancia continua de vulnerabilidades (producción)

**Lo que se vigila.** Escanear `develop` no basta: hay que vigilar lo que **está desplegado**.

**SBOM por versión.**
- Cada versión guarda su SBOM (CycloneDX) junto a los digests desplegados.
- Un workflow diario vuelve a cruzar ese SBOM con las bases de vulnerabilidades (Trivy o Grype
  sobre el SBOM; OSV).
- Así detectamos una CVE **publicada después** de desplegar sin tocar el servidor.

**Dependabot (o Renovate).**
- Cubre `uv.lock`, `pnpm-lock.yaml`, las imágenes base de los Dockerfile, compose y las GitHub
  Actions.
- Abre PRs de seguridad solos y de versión una vez por semana, agrupados.
- Cada PR pasa por las mismas puertas.

**Sistema operativo.**
- `unattended-upgrades` aplica los parches.
- Un script diario en el servidor envía lo pendiente que no se aplica solo: `pro security-status`,
  `apt list --upgradable` y si hace falta reiniciar.

**Componentes de terceros.** LiveKit, `livekit-sip`, Caddy, Postgres y Redis van con versión fijada.
Un aviso de versión nueva (Renovate o RSS de releases) cubre los parches de seguridad.

**Canal de alertas (Telegram o correo).**
- Una CVE crítica o alta en lo desplegado avisa **en el momento**.
- El resto va en un resumen semanal.
- Toda alerta trae el paquete, la versión arreglada y el PR si ya existe.

**Criterio.**
- Una crítica con arreglo se trata en 48 h.
- Una alta, en 7 días.
- Una suprimida lleva justificación y fecha de revisión (regla Confirmada).

### Vigilancia operativa y respuesta

- **Monitor externo (gratis, fuera del servidor)** sobre `/api/health`. Si el servidor cae, el que
  avisa no puede caer con él.
- **GlitchTip** para los errores.
- **Alertas:**
  - llamada terminada por fallo técnico;
  - gasto diario por encima de lo normal;
  - pico de llamadas desde un mismo número;
  - tráfico SIP desde una IP no permitida (lo bloquea el cortafuegos, pero se cuenta);
  - CPU o salida de red anómalas;
  - acceso SSH.
- **Guía de incidente de una página:**
  - rotar cada clave (con su enlace a cada consola);
  - cortar la telefonía (quitar la regla de dispatch o la conexión en Telnyx);
  - restaurar una copia;
  - avisar a la AEPD en 72 h si hay brecha de datos personales.
- **Cuentas:**
  - 2FA, idealmente con llave física, en GitHub, OVH, Telnyx, Google Cloud, AssemblyAI,
    ElevenLabs y el dominio;
  - tokens de API con caducidad y mínimo alcance;
  - en Telnyx y Google Cloud, restricción por IP donde se pueda.

## Ya hecho (no repetir)

- **Sesiones:** opacas y con hash, cookie HttpOnly, SameSite=Lax y Secure, techo de 90 días.
- **OIDC:** con state, nonce y PKCE, y solo el correo verificado cuenta.
- **Rutas:**
  - todas las `/api/*` de negocio exigen sesión o el secreto del agente;
  - webhook de Twilio firmado y token por llamada en `media`.
- **Audio:**
  - URLs firmadas de 5 min con registro de acceso;
  - retención de 14 días;
  - sin *path traversal*.
- **Límites de tamaño** de texto, TTS y audio.
- **LiveKit:** las claves de desarrollo se rechazan fuera de localhost.
- **Web:** cabeceras CSP y similares.
- **Puertos:** compose publica en `127.0.0.1`.
- **Auditoría** por triggers.
- **SQL:** parametrizado, sin SSRF ni deserialización insegura.
- **Secretos:** ni `.env` ni el JSON de GCP están en el historial de git.

**Pendiente de antes:** rotar la clave de AssemblyAI que estuvo en `.env.example`.

## Decisiones pendientes

1. **CI:** GitHub Actions con GitHub Pro (recomendado) o migrar el repositorio a GitLab, como dice
   CLAUDE.md.
2. **Canal de alertas:** Telegram, correo o los dos.
3. **Consola en el piloto:** túnel SSH o Tailscale (recomendado: Tailscale, gratis, sin abrir SSH).
4. **Límites de llamada.** Propuesta:
   - 8 min por llamada;
   - 3 llamadas por número por hora y 10 al día;
   - 5 llamadas a la vez en el piloto;
   - los números ocultos se atienden, pero cuentan juntos en un solo cupo.
5. **Gasto diario** a partir del cual salta el cortacircuitos: propuesta, 20 € en el piloto.
6. **Hosting:** la otra sesión del día trabaja con un VPS de OVH para producción. Hay que
   confirmar que la preproducción efímera vaya en OVH Public Cloud (por horas) y que el Network
   Firewall admite la allowlist de Telnyx.
