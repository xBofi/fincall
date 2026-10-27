# Tareas para activar la telefonía en el VPS de OVH

**Fecha:** 2026-10-23 · **Estado:** Provisional (pendiente de que Andreu lo apruebe) ·
Parte de la spec `../specs/2026-10-22-telnyx-sip-pilot-design.md` (fase 1). Es la «etapa 1» del
plan de escalado que lleva la sesión orquestadora.

## Supuestos del VPS

- **Máquina:** el VPS más barato de OVH (VPS-1: 4 vCPU, 8 GB de RAM y ancho de banda sin límite
  de tráfico, según la oferta de 2025; hay que confirmarlo al contratar). Centro de datos en
  Francia.
- **Todo en una sola máquina:** API, Postgres, Redis, LiveKit, `livekit-sip` y el agente.
  Funciona porque el STT, el LLM y el TTS van por API: el VPS solo mueve audio y estado.
- **Puertos:**
  - SIP en el 5060/UDP.
  - Unos 200 puertos UDP para el audio de `livekit-sip` (con 50 hubo un 408 en local).
  - El rango de audio de LiveKit, para hablar con el agente en la misma máquina.
  - Con G.711 son unos 90 kbps por llamada y sentido.
- **Llamadas simultáneas:** supuesto de 10–15 en 4 vCPU, **sin medir**. Se valida con una prueba
  de carga antes de dar por buena la etapa.
- **Consola:** no se expone a internet; se entra por túnel SSH.

## Andreu

1. Empresa o alta de autónomo. Es el bloqueo para tener un número español.
2. Contratar el VPS y dar acceso SSH con clave.
3. Cuenta de empresa en Telnyx con saldo prepago, y pedir un número español con su documentación.
4. Ticket a Telnyx: que activen SIP REFER y confirmen las tarifas.
5. Aceptar el DPA de Telnyx.
6. Elegir el número humano al que se transfiere (`BC_HANDOFF_NUMBER`). No puede ser el número
   desviado, porque haría un bucle.
7. Poner las claves en el `.env` del VPS.
8. Configurar el desvío del móvil para las pruebas: `**21*`, `**61*` o `**67*`.

## Claude

1. Integrar `feat/sip-local` en `develop` cuando Andreu lo apruebe.
2. Preparar el VPS: Docker, usuario sin root, SSH solo con clave, fail2ban y cortafuegos en dos
   capas (red de OVH y `ufw`). LiveKit en red del host y reglas en `DOCKER-USER`, porque Docker se
   salta `ufw`. Las medidas contra el abuso del trunk las define el estudio de seguridad.
3. Perfil `telephony` en el compose: LiveKit en modo producción, Redis, `livekit-sip` y el agente
   con reinicio automático.
4. `configure_sip`: trunks y regla de dispatch en LiveKit; conexión por IP, número y G.711 A-law
   en Telnyx.
5. Pendientes del código:
   - Despedida «le paso con una persona» cuando el agente transfiere.
   - Guardar `sip.trunkPhoneNumber`.
6. Operación: copia diaria de Postgres fuera del VPS, logs sin números completos, aviso si se cae
   un servicio y migraciones antes de arrancar.
7. Latencia: ajustar el fin de turno de AssemblyAI (≈1 s), que es la palanca principal.

## Validación

- Número del vecino tras el desvío, probado con varios operadores.
- Transferencia real.
- 20–30 llamadas con guion.
- Prueba de carga.

**Criterios:** al menos un 90 % de llamadas sin intervención, `response_ms` p50 ≤ 1200 y
p95 ≤ 1500, transferencia funcionando y ningún secreto en los logs.

## Legal (Pendiente)

- Aviso de tratamiento de datos al inicio de la llamada.
- Decisión sobre si se graba.
