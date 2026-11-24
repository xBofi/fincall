# Telefonía: conectar un número real al asistente

Hay dos caminos:

- **Entrada por SIP con LiveKit (principal, Provisional, 2026-10-22).** El operador (Telnyx) entrega
  la llamada por un trunk SIP a nuestro `livekit-sip`. Somos nuestra propia centralita y podemos
  transferir a una persona.
  Spec: `docs/superpowers/specs/2026-10-22-telnyx-sip-pilot-design.md`.
- **Puente por WebSocket con un operador (respaldo): Twilio Media Streams o Vonage.** Descrito
  más abajo. Los dos implementan la misma interfaz (`CarrierChannel`).

## La interfaz entre operador y asistente

`backend/src/building_call/telephony/carrier.py` es todo lo que el puente (`bridge.py`) sabe del
operador. Un adaptador implementa `CarrierChannel` para hablar *hacia* la llamada y llama al puente
cuando algo llega *desde* ella:

| Hacia la llamada (`CarrierChannel`) | Desde la llamada (métodos de `CallBridge`) |
|---|---|
| `audio_format`: codificación, tasa y trozo (20 ms) | `begin()`: stream autenticado, saludo |
| `send_audio(frame)`: audio del bot en ese formato | `receive_audio(frame)`: audio del vecino |
| `mark(nombre)`: marca tras lo enviado | `mark_played(nombre)`: la marca ya sonó (ledger) |
| `clear()`: vaciar lo encolado (barge-in) | `receive_dtmf(tecla)`: se registra, no cambia el flujo |
| `hang_up()`: colgar, idempotente | `close()`: fin del stream; guarda la grabación |

STT, grafo, TTS, política de barge-in, ledger de entrega, métricas (`response_ms`) y grabación
viven en el puente y son los mismos con cualquier operador. El puente convierte entre el formato
del canal y el del pipeline (PCM16 16 kHz): μ-law 8 kHz para Twilio y linear16 16 kHz para Vonage,
que no necesita ninguna conversión de entrada.

## Twilio vs Vonage

| | Twilio Media Streams | Vonage Voice API (WebSockets) |
|---|---|---|
| Estado en Fincall | Código y tests con proveedor simulado; sin número aún | **Solo con mocks, nunca probado en vivo** (no hay cuenta) |
| Webhook de entrada | `POST /api/telephony/twilio/voice` (form) → TwiML | `GET /api/telephony/vonage/answer` (query; POST JSON si `answer_method=POST`) → NCCO |
| Cómo se conecta el audio | `<Connect><Stream url="wss://…">` con `<Parameter>` | acción `connect` con endpoint `{"type":"websocket","uri","content-type","headers"}` |
| Firma del webhook | `X-Twilio-Signature`: HMAC-SHA1(auth token, URL + parámetros ordenados) | `Authorization: Bearer <JWT HS256>` con el *signature secret*; `iss=Vonage`, `payload_hash`=SHA-256 del cuerpo |
| Autenticar el WebSocket | token HMAC del `CallSid` en `customParameters`, vuelve en `start` | token HMAC del `uuid` en `headers`, vuelve como clave en `websocket:connected` |
| Audio | μ-law 8 kHz, base64 dentro de JSON (`media.payload`) | linear16 (PCM 16 bits LE) 8/16 kHz en mensajes **binarios**; usamos 16 kHz |
| Marca de reproducción | `{"event":"mark"}` → vuelve `mark` | `{"action":"notify","payload":{…}}` → vuelve `websocket:notify` |
| Barge-in | `{"event":"clear"}` | `{"action":"clear"}` → `websocket:cleared` |
| DTMF | evento `dtmf` | evento `websocket:dtmf` (`digit`, `duration`) |
| Colgar | cerrar el WebSocket: sin más TwiML, la llamada termina | cerrar con código 1000: sin más acciones NCCO, la llamada termina |
| Eventos de la llamada | `StatusCallback` (`POST …/estado-llamada`) | `POST /api/telephony/vonage/event` (`started`, `answered`, `disconnected`, `completed`…) |
| Qué cambia en el pipeline | nada | nada: solo el `audio_format` del canal |

Con 16 kHz Vonage entrega al STT el doble de ancho de banda que la red telefónica clásica; si la
llamada viene de la PSTN el audio real sigue siendo de 8 kHz remuestreado, pero nos ahorramos la
decodificación μ-law y su ruido de cuantización.

Activar Vonage (cuando haya cuenta): en la aplicación de Vonage, *Answer URL*
`{BC_PUBLIC_URL}/api/telephony/vonage/answer` (GET), *Event URL*
`{BC_PUBLIC_URL}/api/telephony/vonage/event` (POST), webhooks firmados activados; en el entorno,
`BC_TELEPHONY=vonage` (o `twilio,vonage`) y `BC_VONAGE_SIGNATURE_SECRET` por SecretBroker.
Idioma: el webhook de Vonage no lo trae, así que va en la *Answer URL* de cada número
(`…/answer?language=en`); sin él, `BC_LANGUAGE`. Viaja en las cabeceras del NCCO hasta el puente
(STT, TTS y grafo en ese idioma) y la disculpa de «servicio no disponible» se dice en él.
`GET /api/telephony/vonage/health` dice qué falta. Antes de dar por bueno el adaptador hay que
hacer una llamada real: los campos salen de la documentación oficial (leída el 2026-11-24) y
de tests con tramas falsas, nada más.

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

Estado: código listo y probado con proveedor simulado (`backend/tests/test_telephony.py`). Permite
comprar un número español en minutos y expone el audio de la llamada por WebSocket. Otro proveedor
por WebSocket solo requiere un adaptador nuevo que implemente `CarrierChannel` (`telephony/carrier.py`).

## Qué hace falta de Andreu

1. Cuenta de Twilio con un número de voz (España, `+34…`). En la consola de Twilio: **Account SID**
   y **Auth Token**.
2. Una URL **https pública** que llegue a la API. Para probar desde casa basta un túnel:
   `cloudflared tunnel --url http://localhost:8000` (imprime una URL `https://….trycloudflare.com`).
   En despliegue será el dominio de la API.

## Puesta en marcha (lo hace Claude cuando reciba los datos)

```dotenv
# backend/.env
BC_TELEPHONY=twilio
BC_PUBLIC_URL=https://xxxx.trycloudflare.com       # sin barra final
BC_TWILIO_ACCOUNT_SID=AC...
BC_TWILIO_AUTH_TOKEN=...
BC_TWILIO_NUMBER=+34...
# TTS de servidor (ya configurado en este equipo: Piper con voz es_ES-davefx-medium en backend/.voces)
BC_PIPER_MODEL=<ruta>\es_ES-davefx-medium.onnx
```

```powershell
cd backend
uv run building-call                                        # API (recarga sola al cambiar .env)
uv run python -m building_call.telephony.configure --comprobar   # ¿URL pública, STT, TTS?
uv run python -m building_call.telephony.configure               # fija los webhooks del número
```

El script comprueba `GET {URL}/api/telephony/twilio/health`, que haya `BC_ASSEMBLYAI_API_KEY` y TTS, y
deja en el número `VoiceUrl = {URL}/api/telephony/twilio/voice` (POST) y el `StatusCallback`. Es
idempotente: se puede repetir cada vez que cambie la URL del túnel. Después, **llamar al número**.

## Cómo fluye una llamada

1. Twilio recibe la llamada y hace `POST /api/telephony/twilio/voice` firmado (`X-Twilio-Signature`,
   se valida con el Auth Token; una firma inválida es 403).
2. Respondemos TwiML `<Connect><Stream url="wss://…/api/telephony/twilio/media">` con el teléfono
   del llamante como parámetro. Si la telefonía no está lista (sin TTS, sin STT…), respondemos un
   `<Say>` de disculpa y colgamos, y queda en el log el motivo.
3. Twilio abre el WebSocket y manda audio μ-law 8 kHz; el **puente** (`bridge.py`) lo convierte a
   PCM16 16 kHz y lo pasa a la misma sesión de AssemblyAI (EU) que usa la consola web, con el
   vocabulario de las fincas. Cada fin de turno pasa por `CallManager.message` (mismo grafo).
4. La respuesta se sintetiza con Piper, se convierte a μ-law en trozos de 20 ms y se envía; una
   marca (`mark`) nos avisa cuando ha terminado de sonar (ledger de entrega, igual que en la web).
5. **Barge-in**: si AssemblyAI devuelve un parcial de ≥2 palabras mientras suena el bot, se manda
   `clear` a Twilio y la entrega queda como *interrumpida*.
6. Al terminar la conversación se reproduce la despedida y se cierra el WebSocket: la llamada
   termina. La llamada se guarda en tres pistas alineadas —`mezcla`, `cliente` y `asistente`—
   (privadas, URL firmada, 14 días); aquí la voz del bot es captura real de lo que salió a la
   línea, no una reconstrucción como en la consola web. El
   expediente aparece en la consola con su transcripción, decisiones y latencias
   (`stt_client = phone`).

## Comprobar sin llamar

- `GET /api/telephony/twilio/health` (público, sin secretos): `{"ok": true}` o el motivo.
- `GET /api/telephony/state` (con sesión): proveedor, número, URL pública, TTS/STT.
- `GET /api/health` incluye `telephony` (proveedores si están listos), `server_tts` y `n8n`.

## Latencia esperada

Sobre la medida en la consola (fin de voz → respuesta del grafo p50 1,0–1,6 s con AssemblyAI),
la telefonía añade el TTS de Piper (100–200 ms por frase, precalentado al arrancar) y el tránsito
por Twilio (~100–300 ms por sentido). Objetivo `response_ms` ≤ 1200 sigue siendo alcanzable en
turnos deterministas; los turnos con LLM quedarán en 1,5–2 s. Medir con llamadas reales y ajustar
`BC_ASSEMBLYAI_*` antes de tocar nada más.

## SIP: decisión y diseño

**Decisión (2026-11-24): se integra la rama `feat/sip-local`.** Es sólida: no reinventa SIP, sino
que usa `livekit-sip`, un servidor SIP de verdad (señalización, RTP, códecs, jitter buffer) que
mete cada llamada en una sala de LiveKit con el mismo agente que ya usa la consola. Añade lo
justo: trunk de entrada idempotente con autenticación obligatoria (usuario de ≥16 caracteres o
IPs del operador), regla de despacho con una sala por llamada, número del vecino desde
`sip.phoneNumber`, transferencia a una persona por SIP REFER y un banco automático
(`evals/e2e_sip.py`) que pasó en local. La rama original no se borra; aquí se ha traído su commit
sobre `develop`.

Cómo funciona una llamada SIP:

```text
softphone / operador            livekit-sip (5060, RTP 30000-30199)          agente Fincall
  INVITE sip:+34900…@host  ──▶  auth (digest o IP) + trunk "fincall-entrada"
                           ◀──  100 Trying / 180 Ringing
                                regla "fincall-llamadas" → sala tel-<id> + agente ──▶ api.begin(phone)
                           ◀──  200 OK (SDP: PCMU/PCMA 8 kHz; G.722 desactivado)
  ACK                      ──▶
  RTP (G.711, 20 ms)       ◀─▶  WebRTC (Opus) en la sala  ◀─▶  STT AssemblyAI / grafo / TTS
  …                             REFER a BC_HANDOFF_TARGET si el vecino pide una persona
  BYE                      ◀─▶  la sala se borra al colgar
```

- **INVITE / 200 OK / ACK / BYE**: los gestiona `livekit-sip`; nosotros solo declaramos quién puede
  llamar (trunk) y a qué sala va (regla). `configure_sip` lo deja hecho de forma idempotente.
- **RTP y códecs**: G.711 (PCMU/PCMA, 8 kHz) es lo que habla cualquier línea; desactivamos G.722
  para que no se negocie HD con un softphone y luego se comporte distinto que la red real.
  LiveKit transcodifica a Opus dentro de la sala.
- **Desde un trunk SIP** (Telnyx, o cualquier operador español con SIP): conexión por IP hacia el
  5060 del servidor, `BC_SIP_ALLOWED_ADDRESSES` con sus IPs de señalización y el cortafuegos
  abierto solo a ellas (5060 y el rango RTP).
- **Desde Twilio sin cambiar de operador**: un *Elastic SIP Trunk* con *Origination URI*
  `sip:<ip-o-dominio-del-servidor>` entrega las llamadas del número al mismo `livekit-sip`; o, sin
  trunk, el webhook de voz del número responde TwiML `<Dial><Sip>sip:+34…@<servidor></Sip></Dial>`.
  El trunk de entrada aceptaría las IPs de señalización de Twilio. (Un *SIP Domain* de Twilio es
  el camino contrario: softphones que llaman *hacia* Twilio.) Es la alternativa a Media Streams
  cuando se quiere REFER y medios por RTP en lugar de WebSocket.

Probar en local con un softphone (MicroSIP, Linphone): sección «En local (fase 0)» arriba.

## Pendiente / decisiones abiertas

- Vonage: probarlo con una cuenta real antes de usarlo (hoy solo mocks).

- Confirmar proveedor en la bóveda (Twilio vs. alternativa española con SIP). El puente no cambia.
- ~~Autenticar `/api/llamadas*`~~ Hecho: exigen sesión de la consola; solo `/api/health` y los
  webhooks firmados de Twilio quedan públicos.
- ~~Piper en Docker~~ Hecho: la imagen `api` instala el extra `tts` y descarga la voz en la build.
- ~~Pista del asistente~~ Hecho en telefonía (canal `asistente`, a la tasa del TTS). En la consola
  web sigue sin ser posible: el TTS lo hace el navegador.
