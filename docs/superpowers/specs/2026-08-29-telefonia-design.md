# Telefonía real: puente proveedor-agnóstico + adaptador Twilio

**Fecha:** 2026-08-29 · **Estado:** implementado en `develop`, pendiente de número real.
Guía operativa: `docs/TELEFONIA.md`.

## Objetivo

Que una llamada de teléfono real pase por el mismo grafo, el mismo STT (AssemblyAI EU) y la misma
persistencia que la consola web, sin tocar el grafo ni el gestor. Andreu entrega credenciales y
número; el sistema queda configurado con un comando.

## Decisiones

- **Proveedor**: *Pendiente* en la bóveda. Twilio Media Streams como primera implementación
  (Provisional): número español, audio bidireccional por WebSocket, `mark`/`clear` para saber cuándo
  terminó de sonar y para cortar. El puente (`puente.py`) no importa nada de Twilio; un adaptador
  alternativo (SIP/otro CPaaS) implementa `CanalTelefono` (`enviar_audio`, `marcar`, `limpiar`,
  `colgar`) y llama a `iniciar` / `recibir_ulaw` / `marca_reproducida` / `cerrar`.
- **Audio**: red telefónica μ-law 8 kHz ↔ pipeline PCM16 16 kHz con `audioop` (estado de
  remuestreo entre trozos para evitar clics; salida en trozos de 20 ms = 160 bytes; último trozo
  rellenado con silencio μ-law `0xFF`).
- **TTS de servidor**: puerto `SintetizadorVoz`. Piper **en proceso** (`piper-tts`, extra `tts` de
  `pyproject`), modelo cargado una vez y precalentado en el arranque: medido 100–200 ms por frase
  (por subproceso eran ~2,5 s: descartado). Caché LRU de frases (saludo, preguntas fijas).
  Alternativa Workers AI MeloTTS (`lang=es`) → MP3 → PCM vía ffmpeg, más lenta. `BC_TTS=auto`
  elige Piper si está.
- **Barge-in por transcript parcial** (≥2 palabras mientras suena el bot), no por energía: la
  pista entrante de Twilio solo trae al vecino, y AssemblyAI ya hace la detección de voz mejor que
  un umbral RMS sobre línea telefónica.
- **Seguridad**: webhooks validados con `X-Twilio-Signature` (HMAC-SHA1 sobre URL pública +
  parámetros ordenados); sin firma válida, 403. La URL pública debe ser https. Nada de secretos
  en `/api/telefonia/twilio/salud` (público) ni en logs. `/api/telefonia/estado` exige sesión.
- **Degradación**: si falta TTS/STT/configuración, el webhook responde `<Say>` de disculpa y
  cuelga, y registra el motivo. Si el TTS falla a mitad de llamada, se cuelga (mejor que silencio).
- **Autoconfiguración**: `python -m building_call.telefonia.configurar` comprueba la URL pública,
  STT y TTS, localiza el número en la cuenta y fija `VoiceUrl`/`StatusCallback`. Idempotente.
- **Métricas**: el puente rellena `respuesta_ms` (último audio con energía del vecino → primer
  trozo de audio del bot) y `tts_inicio_ms` con `stt_cliente="telefono"`: aparecen en la consola
  y en `GET /api/metricas` junto a las de la web.

## Verificación

- `tests/test_telefonia.py`: conversión de audio, puente con STT/TTS/canal falsos (saludo, turno,
  barge-in, colgado al final, grabación), firma y TwiML, webhook 403/no-listo/conectar, script de
  configuración contra Twilio simulado (`httpx.MockTransport`).
- Medición manual de Piper (2026-08-29, CPU): precalentar 1,9 s; frases de 2–4,5 s de audio en
  94–208 ms.

## Fuera de alcance

Grabación de la pista del asistente, transferencia a humano, colas/horarios, SMS de confirmación.
