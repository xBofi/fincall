# Telefonía: conectar un número real al asistente

Estado: código listo y probado con proveedor simulado (`backend/tests/test_telefonia.py`). Falta el
número. El proveedor está **Pendiente** en la bóveda; la primera implementación es **Twilio**
(Provisional): permite comprar un número español en minutos y expone el audio de la llamada por
WebSocket, que es lo que el puente necesita. Otro proveedor solo requiere un adaptador nuevo en
`backend/src/building_call/telefonia/` que implemente `CanalTelefono`.

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
   termina. El audio del vecino se guarda como en la web (privado, URL firmada, 14 días) y el
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
