# Delivery Ledger, Phase 2: LiveKit in Development

Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md` (§2, §4). Phase 1
(pieces and ledger in the graph) is in place; this phase adds the transport that measures what
was heard word by word.

## What was built

- **LiveKit server** in `compose.yaml`, profile `voz` (`livekit/livekit-server:v1.13.8 --dev`,
  ports 7880/tcp, 7881/tcp, 7882/udp on 127.0.0.1).
- **Agent worker** `building_call.voz.agente` (`uv run --extra voz building-call-agente start`):
  - AssemblyAI EU, with our own end-of-turn settings and the estates' vocabulary;
  - ElevenLabs PCM, with word timestamps;
  - silero VAD for barge-in.
  - No LiveKit Cloud calls:
    - `turn_detection="stt"`, so the end of turn comes from AssemblyAI;
    - `interruption.mode="vad"`;
    - LiveKit's preemptive generation is off, because the graph has side effects;
    - `record=False`.
  - Our own speculation goes through `POST /api/llamadas/{id}/parcial`.
  - Endpointing `min_delay=0`: AssemblyAI already waits for its own silence.
- **Agent access to the API:** the existing call routes accept `Authorization: Bearer
  BC_AGENTE_SECRETO` as well as a console session. With no secret set, they accept only a
  console session. The agent holds no business logic.
- **Ledger from LiveKit:** on `conversation_item_added` the agent counts the synchronized words
  (`corte_por_palabras`) and reports `texto_entregado`. Completed speech reports the whole text.
- **Close:** after the farewell, `ControlLlamada` decides whether to hang up or to say the cut
  next-step notice again first (Andreu, 2026-10-21: the next step matters, the incident number
  is not spoken).
- **Console:** a «LiveKit» mode (`livekit-client 2.22.3`, loaded on demand). It calls
  `POST /api/livekit/token` (console users only), which returns a room with an
  `fincall` agent dispatch, and shows the call state polled from the API.
- **Bench** `evals/e2e_livekit.py`: the same scenarios as `e2e_voz.py` plus `interrupcion`,
  over a real room, measuring end of caller speech → first bot audio.

## Measured (2026-10-21, Gemini 3.5 Flash Lite, AssemblyAI EU, ElevenLabs v4 Turbo)

- **Baseline, WebSocket + server voice** (`e2e_voz.py` with `BC_BANCO_CON_VOZ=1`): 7/7
  scenarios, `respuesta_ms` p50 1302 ms, p95 3596 ms (n=43).
- **The old bench measured TTS wrongly:** abandoning a `/api/voz-bot` stream after the first
  chunk made the next request wait about 2 s. ElevenLabs' real first audio is 140-230 ms.
  The bench now consumes the whole stream.

## Pending

- LiveKit run of the full bench and the comparison against the baseline.
- Removing the WebSocket path and the browser voice once LiveKit is validated (spec §4).
- Phase 3: SIP.
