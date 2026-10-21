# LiveKit Delivery Ledger Design

## Goal

Give the dialogue graph an exact account, for every bot message, of **what the caller actually
heard and what they did not**, so the next turn can bring back what mattered (a question, the
legal notice) and drop what did not (courtesy, a paraphrase). LiveKit Agents, self-hosted,
becomes the media layer: it is what knows, word by word, how far the audio got before a
barge-in.

Decided by Andreu on 2026-10-21: LiveKit **self-hosted** (Ubuntu/Docker), not LiveKit Cloud. The
audio never leaves our server (RGPD). This replaces the "LiveKit deferred" note in
`2026-08-28-voice-pipeline-latency-design.md`.

## Current state (measured 2026-10-21)

- Browser with Web Speech TTS: `onboundary` gives word boundaries. Browser with server TTS
  (ElevenLabs): the cut is **estimated by time** (~14 chars/s).
- Telephony (Twilio media streams, `telefonia/puente.py`): on barge-in the ledger records a fixed
  **ratio 0.5** whatever was played.
- The graph only reads the ledger in `_no_lo_oyo`: "¿hola?" after a message that was not heard in
  full repeats the whole message. The interpretation LLM never sees what the caller heard, so a
  "sí" that interrupts the summary before its question is read as confirming the summary.

## Design

### 1. The message is a list of pieces with importance

Each bot message is already built from templates (`graph/plantillas.py`). The graph emits the
pieces instead of one flat string:

```python
class Pieza(TypedDict):
    texto: str
    plantilla: str | None          # template id, for audit
    importancia: Literal["pregunta", "aviso", "dato", "cortesia"]
```

- `pregunta`: templates with `pregunta=True`. If it was not heard, it must be asked again.
- `aviso`: the AI and recording notice in the greeting (legal) and the next step at the close
  (Confirmed by Andreu, 2026-10-21). If it was not heard, it is said again before anything else.
  The incident number is not spoken (Confirmed by Andreu, 2026-10-21): the close never reads it.
- `dato`: the summary ("Le resumo: …"), the identified address. If it was not heard and it was
  being confirmed, the confirmation does not count (see §3).
- `cortesia`: "Perfecto", "De acuerdo, lo corrijo". It is dropped and never repeated.

`mensajes_bot` stays as it is for the console and the history; `piezas_bot` is added next to it.

### 2. The ledger: word timings from LiveKit, pieces from the graph

The agent worker (§4) sends each piece to TTS as its own segment (one `llm_node` chunk followed
by a `FlushSentinel`), so every piece gets its own `PlaybackFinishedEvent`. With
`use_tts_aligned_transcript=True`, ElevenLabs gives `TimedString` words with `start_time` and
`end_time`.

For each piece:

| State | Rule |
|---|---|
| `entregada` | the playback finished without interruption |
| `parcial` | interrupted; the words with `end_time <= playback_position` were heard |
| `no_entregada` | the piece never started (it was queued behind the interrupted one) |

`registrar_entrega` changes from `(estado, ratio, texto)` to a list of
`{pieza, estado, texto_oido, texto_no_oido}`. A `parcial` piece with less than 60 % of its words
heard counts as not heard. The flat ratio is kept, computed from the words, so `/api/metricas` and
the console keep working.

Caveats from the LiveKit source (`livekit-agents` 1.8.x):

- `playback_position` is audio pushed minus audio queued. It does not count the network jitter
  buffer or the SIP leg, so it **overstates** what was heard by tens of ms. We subtract a
  configurable margin (`BC_LEDGER_MARGEN_MS`, default 150) and measure the real gap over SIP.
- False interruptions (`resume_false_interruption`, on by default): the audio pauses and resumes
  if the caller says nothing. The ledger records `reanudada` and waits for the final
  `playback_finished`; it does not close the piece on the pause.
- `eleven_v4_turbo` goes through ElevenLabs' text-to-dialogue streaming API; the plugin rebuilds
  the timestamps itself and is only exact with **PCM** output. The worker uses PCM.

### 3. What the next turn does with it

Deterministic, in `_turno` before global intents, so it costs no LLM call:

1. An `aviso` not heard → it is said again in front of the reply.
2. A `pregunta` not heard → the caller's answer is not taken as an answer to it. If the turn
   carries data (an address, a problem), it is still consumed; then the question is asked again.
   A bare "sí"/"no" to an unheard question is not a confirmation: the question is repeated.
3. A `dato` that was being confirmed and was not heard → the summary is read again before
   asking for the confirmation.
4. Only `cortesia` not heard → nothing changes.

The interpretation prompt also receives one line, only when something was cut:
`El vecino interrumpió; solo llegó a oír: "…"`. It costs nothing when there was no barge-in and
lets the model tell "sí" (confirming) from "sí, sí, pero…" (interrupting).

The existing `_no_lo_oyo` stays: it covers "¿hola?, ¿estás ahí?" after an unheard message.

### 4. Architecture

```text
   navegador (livekit-client)          teléfono ── Twilio Elastic SIP ── livekit-sip
              │ WebRTC                                                │
              └──────────────── livekit-server (self-hosted) ─────────┘
                                         │
                          agente (livekit-agents, proceso propio)
                 STT AssemblyAI EU · TTS ElevenLabs PCM · VAD silero
                                         │ HTTP local
                                API FastAPI (GestorLlamadas, grafo, ledger)
```

- **Agent worker** `building_call.voz.agente`, a separate process (`uv run building-call-agente`).
  `GraphAgent.llm_node` takes the committed user transcript, calls the API
  (`POST /api/llamadas/{id}/mensajes`) and yields the returned pieces. A placeholder `llm.LLM`
  is set because LiveKit skips the reply when the session has no LLM. The API stays the only
  brain and the only owner of call state: the worker holds no business logic.
- **Self-hosted, no LiveKit Cloud calls:** `turn_handling={"turn_detection": "stt",
  "interruption": {"mode": "vad"}, "preemptive_generation": {"enabled": False}}`,
  `record=False`. The default turn detector and adaptive interruption call LiveKit's cloud
  gateway, so they are off. End of turn comes from AssemblyAI, as today.
  Preemptive generation is off because the graph has side effects; our own speculation on
  partial transcripts (`clave_especulacion`) stays.
- **Browser console:** `livekit-client` replaces the `/api/llamadas/{id}/voz` WebSocket.
  `POST /api/livekit/token` (logged-in console users only) returns a token with
  `RoomAgentDispatch(agent_name="fincall")`. Web Speech and the old WebSocket stay as the
  fallback until LiveKit is validated, then they are removed.
- **Telephony:** `livekit-sip` + Redis + Twilio Elastic SIP trunk, inbound trunk and an
  individual dispatch rule (`room_prefix="call-"`). It replaces the Twilio media-streams bridge
  (`telefonia/puente.py`). It needs the public number, which is still pending.

Versions, pinned to releases at least two weeks old (checked 2026-10-21): `livekit-agents` and
plugins `1.8.5`, `livekit-api 1.2.1`, `livekit-client 2.22.3`, `livekit/livekit-server:v1.13.8`,
`livekit/sip:v1.17.0`. `livekit-agents 2.0` is in beta and is not used.

## Latency

`respuesta_ms` is measured before and after on the same e2e bench (`evals/e2e_voz.py`). WebRTC
adds a jitter buffer that WebSockets did not have. Target: no more than +100 ms at p50; if it is
more, the reason is documented before going on.

## Phases

1. **Pieces and ledger in the graph** (transport-agnostic, TDD with the fake LLM). `Pieza`,
   the new `registrar_entrega`, the §3 rules and the prompt line. The browser and Twilio report
   per piece with what they have today.
2. **LiveKit dev:** `docker-compose.livekit.yml` (server `--dev`), agent worker, token endpoint,
   console on `livekit-client`. Word-level ledger from `TimedString`. e2e bench and latency
   compared.
3. **LiveKit SIP** on the Ubuntu server once the number exists; the Twilio bridge is removed.

## Pending

- The real gap between `playback_position` and what the caller hears over SIP: measured in
  phase 3.
- ElevenLabs alignment quality for Spanish ordinals and numbers on the dialogue API: checked in
  phase 2.
