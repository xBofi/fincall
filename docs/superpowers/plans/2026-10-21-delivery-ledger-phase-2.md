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

## Turn handling in the LiveKit agent (2026-10-22)

The first LiveKit run of the bench got 3/8 against 7/7 over WebSocket. Measuring, not guessing,
showed three separate causes:

1. **Universal-3 Pro closes the turn at a comma.** The synthetic voice (Windows SAPI) pauses
   ~580 ms at a comma and ~1100 ms at a full stop. With `min_turn_silence=220`, AssemblyAI
   ended the turn after "Sí." and sent "Eso es." as a second turn.
   - STT-only bench: the bench phrases sent straight to AssemblyAI EU, 32 phrases per
     configuration.
   - Count of phrases split into more than one turn:

     | Config | Split | EOT p50 | EOT p90 |
     | --- | --- | --- | --- |
     | WebSocket path as is (U3 Pro by default, `balanced`) | 4/32 | 484 ms | 1046 ms |
     | Agent as it was (U3 Pro, 220/800, `language_codes=es`) | 6/32 | 562 ms | 1062 ms |
     | U3 Pro, `mode=balanced` with no silences | 13/32 | 453 ms | 1453 ms |
     | U3 Pro 250/1000 | 4/32 | 577 ms | 1234 ms |
     | **U3 Pro 300/800** | **1/32** | 657-671 ms | 1094-1155 ms |
     | U3 Pro 300/1000, 350/1000, 400/800, 400/1000 | 1/32 | 655-766 ms | 1046-1250 ms |
     | U3 Pro 300/800 + `agent_context` (the bot's last question) | 3/32 | 578 ms | 1046 ms |
     | `universal-streaming-multilingual` 220/800 | 11/32, two short words lost | 719 ms | 952 ms |
     | `universal-streaming-multilingual` 400/1000 | 5/32, two short words lost | 1031 ms | 1217 ms |

   - Chosen: U3 Pro 300/800 (`BC_LIVEKIT_STT_SILENCIO_MIN_MS` / `_MAX_MS`). The setting is
     separate, so the WebSocket path is unchanged.
   - Chosen: no `agent_context` (`BC_LIVEKIT_STT_CONTEXTO=false`).
   - The multilingual model is discarded: it splits more and loses "sí" and "tercero B".
   - The step is at 300 ms, which matches the voice's pauses. Real people pause less, so a
     larger value would only buy bench accuracy with latency.
   - The pause is in the audio itself, not added by LiveKit. The audio the agent hands to the
     STT has the same gaps as the WAV (580/1100 ms).
   - AssemblyAI does not always split the same audio the same way. Replaying the agent's audio
     gave a different split from the live run.
2. **LiveKit and the API went out of step on a split turn.**
   - When the second piece arrived, LiveKit cancelled the reply to the first, but the API
     request had already gone out.
   - The two turns reached the graph at the same time.
   - LiveKit does not report a reply cut before its first audio, so the graph took it as
     heard. "Eso es." then confirmed a summary the caller never heard.
   - `ControlLlamada` (`voz/control.py`) now does three things:
     - sends turns one at a time and in order, and does not cut a request already sent;
     - before the next turn, marks as `no_reproducida` the reply that LiveKit never reported;
     - sends together whatever the caller said that has not been sent yet.
3. **A split turn should go as one turn.**
   - The agent holds a turn back in two cases:
     - AssemblyAI closed it while the VAD still heard the caller (`stt_node` notes this);
     - the turn has only filler words ("Ah, vale, perdón.", "Mira.", "Hola, buenas."). Here
       it waits 0.8 s to see whether the caller goes on.
   - While held, it waits until the caller has been silent for 1.2 s. If the next piece
     arrives first, LiveKit cancels the wait and both pieces go together.
   - A turn with content, closed in silence, is not held at all.
   - The silero VAD ends speech after 0.15 s. With 0.25 s it still heard the end of the
     sentence when the turn closed, and held whole turns back for 1.2 s.

### Bench results (LiveKit, `evals/e2e_livekit.py`; WebSocket, `evals/e2e_voz.py` with voice)

| Run (2026-10-22) | Correct | `respuesta_ms` p50 | p95 | n |
| --- | --- | --- | --- | --- |
| LiveKit, agent as it was (U3 220/800, no turn control) | 3/8 | 1843 | 2530 | 30 (3 scenarios stalled) |
| LiveKit, U3 300/800 + turns in order + `no_reproducida` | 4/8 | 1625 | 2437 | 48 |
| LiveKit, + no `agent_context` + held turns (VAD 0.25 s) | **5/8** | 1592 | 3469 | 48 |
| WebSocket today (same API and providers, same day) | 5/7 | 1134 | 1869 | 43 |
| WebSocket baseline 2026-10-21 | 7/7 | 1302 | 3596 | 43 |

- The final configuration differs from the last full run only in the VAD (0.15 s instead of
  0.25 s). That change was checked on its own with `natural_todo_junto` (no held turns, p50
  1905 ms) and `interrupcion` (OK, p50 1500 ms), not with another full run.
- The p95 of 3469 ms in the last run comes from one turn held 1.2 s by the old VAD
  (`natural_todo_junto`) and from slow graph turns in `guiado` (3.6 s with no hold).
- **Not 7/8: 5/8.**
  - `guiado` and `natural_todo_junto` also fail over WebSocket today (5/7). The graph no
    longer asks the detail question after "fuga de agua en el baño": the urgency comes out
    high and it goes straight to the summary. The bench script then falls out of step.
  - This is a graph or scenario change since 2026-10-21, not a transport problem.
  - `muletillas_y_saludo` fails only over LiveKit. The turn now arrives in one piece ("Ah,
    vale, perdón. Pues sí. Piso 1, puerta."), but AssemblyAI drops the final "A" in every
    live run (5 of 5). It does not drop it when the same audio is sent outside LiveKit.
  - With the "A" missing, the graph takes the "B" in "Bofi" as the door. That is a graph bug
    to add to `gold_interpretacion.jsonl`.
  - `portal_ambiguo`: AssemblyAI hears "El tercero es" for "El tercero A es" about half the
    time, over both LiveKit and WebSocket.
- Latency: LiveKit is ~450 ms slower at p50 than WebSocket today.
  - About 90 ms of that is the 300 ms silence.
  - Most of the rest is the audio path itself: WebRTC, playback and ElevenLabs streaming up to
    the first audio that actually sounds.
  - The WebSocket bench measures TTS first audio over HTTP, not audio played back.
  - A held turn adds 0.8-1.2 s, and is only used when the caller has not finished.
- Concurrency: the agent's name is configurable (`BC_LIVEKIT_AGENTE`, default `fincall`) so a
  test agent can share the same LiveKit server (`--agente fincall-pruebas --api ...`).
- The voice WebSocket accepts the agent secret, like the other call routes, so the
  `e2e_voz.py` bench can sign in now that password login is gone.

## Pending

- Interruption tuning (`min_duration`, `min_words`) was left at its defaults
  (`BC_LIVEKIT_INTERRUPCION_MIN_S` / `_MIN_PALABRAS` exist). The `interrupcion` scenario
  passes, and no case was measured where it broke a turn into several.
- The "A" dropped in the LiveKit audio (see above).
- `guiado` and `natural_todo_junto`: review whether a leak should skip the detail question
  (product rule), and update the scenarios or the graph accordingly.
- Removing the WebSocket path and the browser voice once LiveKit is validated (spec §4).
- Phase 3: SIP.
