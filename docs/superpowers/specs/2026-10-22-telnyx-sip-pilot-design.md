# Prueba de telefonía: Telnyx por trunk SIP + LiveKit SIP

**Fecha:** 2026-10-22 · **Estado:** Provisional (propuesta, pendiente de aprobación) ·
Sustituye como camino principal al puente Twilio Media Streams
(`2026-08-29-telefonia-design.md`), que queda como respaldo.

## Objetivo

Comprobar con llamadas reales que el modelo «nuestra propia centralita» funciona antes de
invertir en escalado y documentación:

1. Un número español de Telnyx recibe llamadas **desviadas** desde la línea de un administrador
   (en la prueba, un móvil de pruebas) y el bot las atiende.
2. Desde la llamada, el bot **transfiere** a un teléfono humano cuando el vecino lo pide.
3. Medimos lo que decide si seguimos: `response_ms` por teléfono, si llega el número del vecino
   tras el desvío, calidad del audio a 8 kHz con AssemblyAI y si las transferencias funcionan.

Si funciona: fase siguiente con documentación, número → administrador, canales y escalado.
Si no: se documenta por qué y se prueba el respaldo (Twilio).

## Fases de la prueba

- **Fase 0 (sin número ni empresa):** softphones SIP (Linphone/Zoiper) llaman directamente a
  `livekit-sip` del VPS, con G.711 forzado para imitar el audio telefónico. Valida el servidor,
  el agente por SIP, la latencia y la transferencia (a otro softphone). Telnyx no interviene.
- **Fase 1 (con número):** número de Telnyx (u otro operador si Telnyx no acepta particulares),
  desvío real desde un móvil y transferencia a un teléfono real. Solo añade lo que la fase 0 no
  puede medir: el desvío, el número del vecino tras el desvío y la red telefónica real.

## Decisiones

- **Camino de la llamada**: Telnyx (número + conexión SIP) → `livekit-sip` → sala de LiveKit →
  el agente existente (`building_call.voice.agent`) → API. El grafo, el STT (AssemblyAI UE), el
  TTS (ElevenLabs) y la persistencia no cambian.
- **Por qué SIP y no el WebSocket de medios de Telnyx**: es la arquitectura que escala
  (centralita propia, varios trunks, transferencias por SIP REFER) y reutiliza el agente de
  LiveKit. El puente Twilio no hace transferencias.
- **Dónde corre**: todo en **un VPS en la UE** (API, Postgres, LiveKit, `livekit-sip`, agente)
  con `docker compose --profile telephony`. SIP y audio no funcionan bien detrás del NAT de casa,
  y un túnel añadiría latencia. La consola sigue siendo «solo localhost»: se entra por túnel SSH
  (`ssh -L 8080:localhost:8080`), sin exponerla.
- **Seguridad**:
  - El cortafuegos del VPS solo abre SIP (5060) y el rango UDP de audio a las IPs de señalización
    y medios de Telnyx.
  - Telnyx autentica contra LiveKit por IP.
  - El trunk de entrada de LiveKit solo acepta el número de la prueba.
  - Secretos solo en el `.env` del VPS.
  - Nada de números completos de vecinos en los logs.
- **Número del vecino**: el agente lo toma del atributo SIP del participante (`sip.phoneNumber`)
  en vez de los metadatos del dispatch. El número llamado (`sip.trunkPhoneNumber`) se guarda en
  la llamada para la futura tabla número → administrador. En la prueba hay un solo administrador.
- **Transferencia**: cuando el grafo termina en `handoff_to_human` y hay `BC_HANDOFF_NUMBER`, el
  agente dice la despedida y hace `TransferSIPParticipant` (REFER) a ese número en vez de colgar.
  Sin número configurado o si la transferencia falla, se comporta como hoy: el caso queda para el
  administrador. El número de transferencia **no puede ser el número público desviado** (bucle).
- **Audio**: códec G.711 A-law (el de la red española) entre Telnyx y LiveKit. LiveKit lo
  convierte para el agente. Se mide si AssemblyAI pierde exactitud con audio telefónico.

## Lo que hace el responsable (no se puede automatizar)

1. Cuenta de empresa en Telnyx: verificación y recarga de saldo prepago.
2. Pedir un número español y subir la documentación que pida Telnyx (empresa y dirección).
3. Ticket a Telnyx:
   - que activen **SIP REFER** (transferencias) en la cuenta;
   - que confirmen la tarifa del minuto entrante y saliente en España.
4. Contratar el VPS (Hetzner o similar, Alemania o Finlandia, 4 vCPU / 8 GB) y darme acceso SSH.
   Opcionalmente, un subdominio para TLS.
5. Configurar el desvío en su móvil durante las pruebas.
6. Las credenciales van al `.env` del VPS, nunca por el chat.

## Lo que hace Claude

1. **Compose**, perfil `telephony`: `livekit-server` en modo producción (claves propias, IP
   pública, Redis), `livekit-sip`, el agente como servicio y Caddy con TLS para LiveKit y la API.
2. **Configuración idempotente** (`python -m building_call.telephony.configure_sip`):
   - crea en LiveKit el trunk de entrada, la regla de dispatch hacia `BC_LIVEKIT_AGENT` y el
     trunk de salida;
   - en Telnyx, por API, crea la conexión SIP, le asigna el número y crea el perfil de salida.
   - Probado con transportes simulados, como el actual `configure.py` de Twilio.
3. **Agente**:
   - lee el llamante y el número llamado de los atributos SIP;
   - transfiere en `handoff_to_human`;
   - etiqueta las métricas con `stt_client="sip"`.
4. **Tests (TDD)**: lectura de atributos SIP, decisión de transferir o colgar, y configuración
   contra Telnyx y LiveKit simulados.
5. **Medición**:
   - `evals/e2e_livekit.py` contra el VPS;
   - 20–30 llamadas reales con guion (avería simple, dirección difícil, pedir persona, colgar a
     mitad);
   - informe con p50/p95 de `response_ms` y los resultados.

## Criterios de éxito

- ≥ 90 % de llamadas de prueba completadas sin intervención.
- `response_ms` p50 ≤ 1200 y p95 ≤ 1500.
- El número del vecino llega en el desvío desde al menos un operador probado, o queda documentado
  qué operadores lo pierden.
- La transferencia a humano funciona.
- Ningún secreto ni número completo en los logs.

## Resultados de la fase 0 (2026-10-22, local)

Banco `evals/e2e_sip.py`: LiveKit marca por SIP a su propia entrada, así que el audio cruza **dos**
tramos SIP; con un teléfono real habrá solo uno. Se negoció G.711 (PCMU), con G.722 desactivado.

| | WebRTC (`e2e_livekit.py`) | SIP con G.711 (`e2e_sip.py`) |
|---|---|---|
| Escenarios correctos | 4/8 | 6/9 (incluye `pide_persona`) |
| `response_ms` p50 | 1.797 ms | 2.437 ms |
| `response_ms` p95 | 2.828 ms | 2.953 ms |

- **Entendimiento.** Las transcripciones son iguales por SIP y por WebRTC: el audio a 8 kHz no
  empeora a AssemblyAI en estos escenarios. Los tres escenarios que fallan, fallan igual sin SIP
  (grafo o guion).
- **Latencia.** Desglose con reloj de pared en un escenario: el SIP doble añade unos 260 ms hasta
  que llega la transcripción y unos 100 ms hasta que suena el bot. Por tramo, del orden de 150–200 ms.
- **Dónde está el grueso.** Lo que más pesa en ambos casos es la espera de fin de turno: ~1 s desde
  que el vecino calla hasta la transcripción final. Esa es la palanca de latencia, no el SIP.
- **Transferencia.** El agente pide la transferencia y `livekit-sip` envía el REFER. En local el
  otro extremo es el propio banco, que responde 405 (no acepta REFER), y el agente cuelga como
  estaba previsto. La aceptación real del REFER se prueba con Telnyx.
- **Puertos.** Con 50 puertos RTP, una llamada falló con 408 (los puertos tardan en liberarse).
  Con 200 no ha vuelto a pasar.
- **Regresión encontrada de paso.** El bot decía «Tomo nota: floor tercero». Corregido.

Pendiente para la fase 1:
- Con transferencia configurada, la despedida de `handoff_to_human` debe decir «le paso con una
  persona», no «envío un resumen al administrador».
- Guardar el número llamado (`sip.trunkPhoneNumber`) para la tabla número → administrador.

## Fuera de alcance (fase siguiente, si la prueba sale bien)

- Tabla número → administrador.
- Horarios por administrador.
- Varios números.
- Pago por canales.
- Trunk de respaldo con Twilio.
- Alta automática de administradores.
- Alta disponibilidad (2× `livekit-sip`, varios workers).
- Documentación en la bóveda y en la web del equipo.
- Plan de escalado actualizado.
