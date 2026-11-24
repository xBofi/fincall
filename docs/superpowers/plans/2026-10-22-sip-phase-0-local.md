# Plan: telefonía SIP, fase 0 en local

Spec: `docs/superpowers/specs/2026-10-22-telnyx-sip-pilot-design.md`. Rama `feat/sip-local`.

## Tareas

1. **Compose.** Hecho.
   - `redis` y `livekit-sip` (v1.17.0) en el perfil `voice`, compartiendo la red de `livekit`.
   - SIP en 5060 y RTP en 30000–30199.
   - G.722 desactivado para forzar G.711.
2. **`telephony/sip.py`** (TDD, `tests/test_sip.py`). Hecho.
   - Validación sin secretos.
   - Trunk de entrada y regla de despacho, creados o actualizados por nombre.
   - Número del vecino desde los atributos SIP.
   - Decisión de transferir.
3. **`telephony/configure_sip.py`**: CLI idempotente. Hecho.
4. **Agente**. Hecho.
   - Por SIP, crea la llamada con `sip.phoneNumber`.
   - Métricas con `stt_client=sip`.
   - Publica `fincall.call_id` en la sala.
   - Transfiere (REFER) en `handoff_to_human` si hay `BC_HANDOFF_TARGET`.
5. **Banco `evals/e2e_sip.py`**. Hecho.
   - LiveKit llama por SIP a su propia entrada, con los mismos escenarios que `e2e_livekit.py` más `pide_persona`.
   - Si el bot cierra antes de lo que prevé el guion, se evalúa en ese momento.
6. **Medición frente a WebRTC** con el mismo agente y la misma API. Hecho; resultados en la spec.
7. **Transferencia en local**: hecho hasta donde se puede. El REFER sale; el banco no lo acepta (405) y se cuelga. Falta probarlo con Telnyx.
8. **Documentación**: `docs/TELEFONIA.md` y `.env.example`. Hecho. La bóveda y la web, al integrar en `develop`.

## Encontrado de paso

- Regresión del paso del código a inglés: el bot decía «Tomo nota: floor tercero». Corregido en
  `graph/nodes.py`, con aserción en `test_partial_home_data_accumulates`.
