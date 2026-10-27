# Plan de escalado: de la demo a varias fincas

Fecha: 2026-10-10 · Estado: **Provisional** (propuesta para revisar con Andreu y Marc) ·
Origen: auditoría con 5 agentes (concurrencia, rendimiento, código backend, frontend, infraestructura).

> Capacidad e infraestructura revisadas en [2026-10-23-call-scaling-architecture-design.md](2026-10-23-call-scaling-architecture-design.md).

Las cifras de coste y capacidad son **estimaciones** razonadas a partir del código, no medidas.
Hay que validarlas con una prueba de carga antes de comprometerlas con un cliente.

## Fases

| Fase | Qué es | Volumen orientativo |
|---|---|---|
| 0. Demo | Consola en el portátil + túnel | 1 llamada simulada |
| 1. Piloto | Un número de teléfono real, llamadas reales de prueba | < 50 llamadas/día |
| 2. Una finca | Producción en una finca, con administrador real | decenas/día, 1–3 a la vez |
| 3. Varias fincas | Varios administradores, cada uno ve solo lo suyo | cientos/día, 10–40 a la vez |

## Ya corregido (2026-10-10)

- **Especulación compartida entre llamadas** (bug de privacidad): la interpretación anticipada de
  una llamada podía acabar en el expediente de otra. Ahora va por `llamada_id`.
- **Silencio ante fallos** en teléfono: si un turno falla o tarda más de 10 s, el bot pide que se
  repita; al segundo fallo se despide, deriva al administrador y deja la llamada para revisar.
  Si el STT se corta, lo mismo.
- **Memoria que no se liberaba**: el estado de las llamadas terminadas (15 min) o abandonadas
  (2 h) sale de memoria; el índice de métricas y el limitador de login ya no crecen sin límite.
- **Bucle de eventos bloqueado**: scrypt (login) y el borrado de grabaciones van a un hilo; el
  cerrojo de Piper ya no se suelta mientras el hilo sigue sintetizando.
- **Base de datos**: índices para historial, auditoría y configuración vigente (migración 0005);
  pool explícito con `pre_ping`; el resumen de la consola calcula las medianas en una pasada.
- **Consola**: micrófono/WebSocket que quedaban abiertos al colgar o salir; errores técnicos
  traducidos; pantallas secundarias cargadas bajo demanda (−27 % en la carga inicial); el
  historial se refresca mientras está a la vista, no tras cada turno de voz.

## Antes del piloto (bloqueante)

1. **Aviso de grabación y privacidad en el saludo** (RGPD): hoy solo se dice si el vecino pregunta.
   Texto a validar por legal. (S)
2. **Avisos reales**: hoy el correo al profesional/administrador es simulado (`correo_simulado`).
   Sin esto, el bot promete un aviso que no se envía. SMTP o servicio transaccional en la UE. (M)
3. **Salida humana**: `<Dial>` al teléfono de la administración para urgencias o si el vecino pide
   una persona; número de respaldo en la locución de error. (M)
4. **Papeles de Cloudflare**: el RGPD no exige que los datos se queden en la UE. Cloudflare actúa
   como encargado (DPA), está certificado en el Data Privacy Framework UE-EE. UU. con cláusulas
   tipo de respaldo, y no usa los datos para entrenar. Lo que no documenta es fijar la inferencia
   de Workers AI en la UE. Hace falta: DPA firmado, Cloudflare en la lista de subencargados,
   mención en la información al vecino y una EIPD (voz + datos personales). Revisión legal. (S)
5. **Despliegue mínimo**: Caddy con TLS/wss, healthcheck que falle si el LLM no responde,
   migraciones como paso del deploy (sin la semilla), CORS real, sin `admin/admin` ni contraseña
   de BD por defecto. (S)
6. **Copias de seguridad**: `pg_dump` nocturno a almacenamiento UE y una restauración probada. (S)
7. **Alertas**: GlitchTip + monitor de `/api/salud`; alerta cuando una llamada termina por fallo
   técnico (`motivo_revision`). (S)
8. **CI en GitLab**: pytest, ruff, mypy (hoy 21 errores en modo estricto), bandit, build. (M)
9. **Papeles**: DPA con el administrador y con Twilio, AssemblyAI, el hosting; registro de
   actividades; retención también para la transcripción (hoy solo caduca el audio). (M, legal)

## Para una finca en producción

10. **Número → finca**: pasar el `To` de Twilio y una tabla `numeros(e164 → finca)`. El bot se
    salta la pregunta de la dirección: menos latencia y menos errores. (M)
11. **Despliegues sin cortar llamadas**: drenar (no aceptar nuevas, esperar las vivas) y persistir
    el estado del turno para que un reinicio no pierda la llamada. (M)
12. **Medir latencia y exactitud** con el proveedor LLM elegido (`evals/comparar_modelos.py`). (S)
13. **Persistencia del turno fuera del camino crítico**: hoy se hacen 3+N transacciones antes de
    responder; agruparlas en una y moverlas detrás de la respuesta. (M)

## Para varias fincas

14. **Multi-administrador** (lo más grande): `usuarios.administrador_id` y filtro por
    administrador en todas las consultas (llamadas, incidencias, audio, catálogos, métricas), con
    tests de acceso cruzado. Hoy cualquier usuario de la consola lo ve todo. (L)
15. **Catálogo a escala**: la consola pide el detalle de cada finca por separado (N peticiones,
    ~8 consultas cada una) y el bot carga todas las fincas en cada turno de dirección. Endpoint
    en bloque, caché invalidada al editar, y búsqueda con `pg_trgm`. (M)
16. **Paginación** de historial e incidencias (hoy hasta 500/1000 filas de golpe). (M)
17. **Varias réplicas**: el estado vivo de la llamada está en memoria del proceso. Una llamada
    telefónica vive en un solo WebSocket, así que basta con afinidad por llamada + estado
    persistido; métricas y limitador de login a BD/Prometheus. (L)
18. **Audio en almacenamiento de objetos UE** con cifrado y caducidad de 14 días. (M)
19. **TTS en paralelo**: un pool de voces Piper en vez de un único cerrojo, y frases fijas
    pre-sintetizadas al arrancar. (M)
20. **Prueba de carga** con N llamadas Twilio simuladas; subir cuotas de AssemblyAI y Twilio. (M)

## Capacidad estimada (un proceso, sin prueba de carga)

- Audio y WebSockets no son el límite (100+ llamadas).
- **Primer cuello de botella: el LLM.** Con Ollama en una GPU: ~5–8 llamadas simultáneas
  manteniendo p95 ≤ 1,5 s. Con un proveedor en la nube: ~15–25, luego limita Piper.

## Infraestructura propuesta (costes estimados, EUR/mes)

| Fase | Propuesta | Fijo |
|---|---|---|
| Demo | Portátil + túnel | 0 |
| Piloto | VPS CPU en la UE (app + Piper) + LLM por API (Cloudflare) + STT AssemblyAI UE | 30–60 + uso |
| Una finca | Lo anterior + Postgres gestionado UE + staging + observabilidad | +40–80 |
| Varias | 2 nodos de app, APIs de LLM/STT/TTS, Postgres gestionado, almacenamiento de objetos | 150–400 + uso |

Coste variable por llamada de 3 min (estimado): Twilio ~0,03–0,06 € + AssemblyAI ~0,01 € +
LLM < 0,01 € → **~0,05–0,08 € por llamada**. El coste fijo domina hasta miles de llamadas/mes.

## Decisiones pendientes de Andreu

- **Proveedores por API** (decidido por Andreu el 2026-10-10): LLM en Cloudflare Workers AI, sin
  GPU propia. Una GPU no carga un modelo por llamada (sirve las peticiones en lote y solo ~1 de
  cada 2–3 turnos llama al LLM), pero las colas en picos y operar el hardware no compensan
  frente a la elasticidad de una API. En producción, `BC_LLM=workers_ai` (no `carrera`, que
  también manda cada petición a Ollama).
- **STT y TTS**: comparar con mediciones propias antes de decidir. STT: AssemblyAI UE (actual,
  residencia UE) frente a Deepgram Nova-3/Flux en Workers AI (español sin confirmar). TTS: Piper
  (CPU, sin coste ni salida de datos) frente a Deepgram Aura-2 en español en Workers AI
  (`@cf/deepgram/aura-2-es`) y ElevenLabs Flash v2.5 (residencia UE solo en Enterprise). Criterio:
  tiempo hasta el primer audio y calidad en una escucha a ciegas.
- **Canal de aviso real** (correo, SMS, llamada) y a quién en cada urgencia.
- **Transferencia a persona** en el piloto: sí/no y a qué número.
- **Hosting** (Hetzner, OVH, Scaleway…).

## Deuda de código (sin urgencia)

- `graph/nodos.py` (1.850 líneas) y `api/main.py` (700): separar texto/turno/enrutador/nodos y
  cableado/rutas/voz. Movimientos mecánicos con los tests en verde.
- La voz de la consola web y el puente telefónico repiten el mismo bucle (STT → turno → métricas
  → grabación): extraer una clase común, midiendo latencia antes y después.
- Tipar `Dependencias.repo` como repositorio completo (quita 23 `type: ignore`).
- `RepositorioPostgres` no tiene tests de comportamiento: correr los mismos tests de contrato
  contra memoria y Postgres.
- `audioop` desaparece en Python 3.13.
- `App.tsx` (630 líneas): extraer `useLlamadaVoz` y la pantalla de simulación.
