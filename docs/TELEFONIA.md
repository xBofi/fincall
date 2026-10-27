# Telefonía: conectar un número real al asistente

Hay dos caminos:

- **Entrada por SIP con LiveKit (principal, Provisional, 2026-10-22).** El operador (Telnyx) entrega
  la llamada por un trunk SIP a nuestro `livekit-sip`. Somos nuestra propia centralita y podemos
  transferir a una persona.
  Spec: `docs/superpowers/specs/2026-10-22-telnyx-sip-pilot-design.md`.
- **Puente Twilio Media Streams (respaldo).** Descrito más abajo.

## Entrada por SIP (LiveKit)

Camino de una llamada: operador o softphone → `livekit-sip` (trunk de entrada
`fincall-entrada`, que solo acepta `BC_SIP_NUMBER` y autentica con usuario o con las IPs del
operador) → regla `fincall-llamadas`, que crea una sala `tel-…` por llamada con el agente
`BC_LIVEKIT_AGENT` → agente (`building_call.voice.agent`) → API.

El agente toma el número del vecino del atributo SIP `sip.phoneNumber`, etiqueta las métricas con
`stt_client=sip` y publica `fincall.call_id` en la sala.

Si el grafo termina en `handoff_to_human` y hay `BC_HANDOFF_TARGET`, transfiere la llamada (SIP
REFER) en vez de colgar. `BC_HANDOFF_TARGET` nunca puede ser el número de entrada, porque la llamada
volvería al bot.

### En local (fase 0, sin operador)

```powershell
docker compose --profile voice up -d livekit redis livekit-sip
cd backend
uv run python -m building_call.telephony.configure_sip     # trunk + regla, idempotente
uv run building-call                                       # API
uv run --extra voice building-call-agent dev               # agente
```

- **Banco automático:** `uv run --extra voice python evals/e2e_sip.py --verbose`. LiveKit marca por
  SIP a su propia entrada (trunk de salida `fincall-banco-salida`) y un vecino sintético habla con
  el bot.
- **A mano:** un softphone (MicroSIP, Linphone) con servidor `127.0.0.1`, usuario
  `BC_SIP_AUTH_USERNAME` y la contraseña del `.env`, llamando a `BC_SIP_NUMBER`.

Detalles del compose:
- `livekit-sip` comparte la red de `livekit`.
- El audio RTP va por 30000–30199/udp. El 10010 lo ocupa un servicio de NVIDIA en Windows.
- G.722 está desactivado para que se negocie G.711, el audio de una línea normal.

### Con Telnyx (fase 1)

En Telnyx:
- un número español;
- una conexión SIP por IP hacia la IP pública del servidor;
- REFER activado (por ticket).

En el `.env` del servidor:
- `BC_SIP_NUMBER` = ese número;
- `BC_SIP_ALLOWED_ADDRESSES` = las IPs de señalización de Telnyx;
- sin usuario.

Después, `configure_sip`. El cortafuegos solo abre 5060 y el rango RTP a las IPs de Telnyx.

## Puente Twilio Media Streams (respaldo)

Estado: código listo y probado con proveedor simulado (`backend/tests/test_telefonia.py`). Permite
comprar un número español en minutos y expone el audio de la llamada por WebSocket. Otro proveedor
por WebSocket solo requiere un adaptador nuevo que implemente `CanalTelefono`.

## Qué hace falta de Andreu

1. Cuenta de Twilio con un número de voz (España, `+34…`). En la consola de Twilio: **Account SID**
   y **Auth Token**.
2. Una URL **https pública** que llegue a la API. Para probar desde casa basta un túnel:
   `cloudflared tunnel --url http://localhost:8000` (imprime una URL `https://….trycloudflare.com`).
   En despliegue será el dominio de la API.

## Puesta en marcha (lo hace Claude cuando reciba los datos)

```dotenv
# backend/.env
BC_TELEFONIA=twilio
BC_URL_PUBLICA=https://xxxx.trycloudflare.com       # sin barra final
BC_TWILIO_ACCOUNT_SID=AC...
BC_TWILIO_AUTH_TOKEN=...
BC_TWILIO_NUMERO=+34...
# TTS de servidor (ya configurado en este equipo: Piper con voz es_ES-davefx-medium en backend/.voces)
BC_PIPER_MODELO=<ruta>\es_ES-davefx-medium.onnx
```

```powershell
cd backend
uv run building-call                                        # API (recarga sola al cambiar .env)
uv run python -m building_call.telefonia.configurar --comprobar   # ¿URL pública, STT, TTS?
uv run python -m building_call.telefonia.configurar               # fija los webhooks del número
```

El script comprueba `GET {URL}/api/telefonia/twilio/salud`, que haya `BC_ASSEMBLYAI_API_KEY` y TTS, y
deja en el número `VoiceUrl = {URL}/api/telefonia/twilio/voz` (POST) y el `StatusCallback`. Es
idempotente: se puede repetir cada vez que cambie la URL del túnel. Después, **llamar al número**.

## Cómo fluye una llamada

1. Twilio recibe la llamada y hace `POST /api/telefonia/twilio/voz` firmado (`X-Twilio-Signature`,
   se valida con el Auth Token; una firma inválida es 403).
2. Respondemos TwiML `<Connect><Stream url="wss://…/api/telefonia/twilio/media">` con el teléfono
   del llamante como parámetro. Si la telefonía no está lista (sin TTS, sin STT…), respondemos un
   `<Say>` de disculpa y colgamos, y queda en el log el motivo.
3. Twilio abre el WebSocket y manda audio μ-law 8 kHz; el **puente** (`puente.py`) lo convierte a
   PCM16 16 kHz y lo pasa a la misma sesión de AssemblyAI (EU) que usa la consola web, con el
   vocabulario de las fincas. Cada fin de turno pasa por `GestorLlamadas.mensaje` (mismo grafo).
4. La respuesta se sintetiza con Piper, se convierte a μ-law en trozos de 20 ms y se envía; una
   marca (`mark`) nos avisa cuando ha terminado de sonar (ledger de entrega, igual que en la web).
5. **Barge-in**: si AssemblyAI devuelve un parcial de ≥2 palabras mientras suena el bot, se manda
   `clear` a Twilio y la entrega queda como *interrumpida*.
6. Al terminar la conversación se reproduce la despedida y se cierra el WebSocket: la llamada
   termina. La llamada se guarda en tres pistas alineadas —`mezcla`, `cliente` y `asistente`—
   (privadas, URL firmada, 14 días); aquí la voz del bot es captura real de lo que salió a la
   línea, no una reconstrucción como en la consola web. El
   expediente aparece en la consola con su transcripción, decisiones y latencias
   (`stt_cliente = telefono`).

## Comprobar sin llamar

- `GET /api/telefonia/twilio/salud` (público, sin secretos): `{"ok": true}` o el motivo.
- `GET /api/telefonia/estado` (con sesión): proveedor, número, URL pública, TTS/STT.
- `GET /api/salud` incluye `telefonia` (proveedor si está lista) y `tts_servidor`.

## Latencia esperada

Sobre la medida en la consola (fin de voz → respuesta del grafo p50 1,0–1,6 s con AssemblyAI),
la telefonía añade el TTS de Piper (100–200 ms por frase, precalentado al arrancar) y el tránsito
por Twilio (~100–300 ms por sentido). Objetivo `respuesta_ms` ≤ 1200 sigue siendo alcanzable en
turnos deterministas; los turnos con LLM quedarán en 1,5–2 s. Medir con llamadas reales y ajustar
`BC_ASSEMBLYAI_*` antes de tocar nada más.

## Pendiente / decisiones abiertas

- Confirmar proveedor en la bóveda (Twilio vs. alternativa española con SIP). El puente no cambia.
- ~~Autenticar `/api/llamadas*`~~ Hecho: exigen sesión de la consola; solo `/api/salud` y los
  webhooks firmados de Twilio quedan públicos.
- ~~Piper en Docker~~ Hecho: la imagen `api` instala el extra `tts` y descarga la voz en la build.
- ~~Pista del asistente~~ Hecho en telefonía (canal `asistente`, a la tasa del TTS). En la consola
  web sigue sin ser posible: el TTS lo hace el navegador.
