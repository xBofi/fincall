# Voice Pipeline and Latency Design

## Goal

Make the call feel natural while keeping LangGraph in control, and drive the response latency —
**end of the caller's speech to start of the assistant's speech** — down to Andreu's targets:

| Response latency | Rating |
|---|---|
| ≤ 700 ms | excelente (probably out of reach) |
| ≤ 1200 ms | objetivo |
| ≤ 1500 ms | tolerable |
| ≤ 2000 ms | mal |
| > 2000 ms | muy mal |

Every change to the pipeline is measured against these thresholds (p50 and p95) with the tooling
described below. No paid APIs in this phase: local Ollama, browser Web Speech API or local
open-source models. LiveKit stays deferred; the API contract is transport-agnostic so a LiveKit
agent can later replace the browser as the client. Reference design: `xBofi/andreu-portfolio`.

## Pipeline

```text
vecino habla ──▶ VAD (fin de turno) ──▶ STT ──▶ interpretación (0–1 LLM) ──▶ grafo ──▶ TTS ──▶ audio
                 │ silencio ~550 ms      │ navegador o whisper   │ ~3 ms sin LLM / ~700 ms con LLM     │ onstart
                 └── finVozAt ────────────────────────────── respuesta_ms ────────────────────────────┘
```

- **VAD (browser):** energy-based, 200 ms onset, configurable silence window (default 550 ms).
  `finVozAt` = start of the closing silence; the silence window itself is part of the budget.
- **STT:** `navegador` (Web Speech API, Chrome/Edge) or `servidor` (faster-whisper, GPU). Both
  measured; the console lets you switch.
- **Interpretation:** one LLM call at most per turn, and none when a deterministic path suffices
  (yes/no, short address, `3º B`, plain name, FAQ question). Full-name JSON keys, one line, no
  nulls; classification fused into the same call when a problem is described.
- **Graph:** `siguiente_paso` (deterministic) consumes everything the caller already said and
  skips answered questions; global intents (`repetir`, `corregir`, `humano`, `colgar`,
  `fuera_ambito`, `pregunta`) are handled from any node.
- **TTS:** browser `SpeechSynthesis`; `onstart` gives the real audio start. Barge-in: mic energy
  while speaking cancels the utterance.

## Measurements (RTX 4070 SUPER, Ollama 0.23.3, 2026-08-28)

Interpretation prompt format vs qwen3.5:4b (`evals/comparar_modelos.py`, 27 gold utterances):

| Format | Accuracy | p50 | Output tokens |
|---|---|---|---|
| long keys + nulls, pretty JSON | 98 % | 1170 ms | 71 |
| short keys (`pi`, `pu`…), no nulls | 82 % | 554 ms | 15 |
| **full keys, one line, no nulls** | **96 %** | **611 ms** | ~25 |

Model comparison with the chosen format:

| Model | Interp. accuracy | p50 | Classification cat/urg |
|---|---|---|---|
| **qwen3.5:4b** | **96 %** | 611 ms | 88 % / 88 % |
| qwen3.5 (9B) | 88 % | 624 ms | 94 % / 100 % |
| qwen3:1.7b | 71 % | 302 ms | 62 % / 88 % |
| gemma3:1b | 43 % | 454 ms | 56 % / 62 % |
| qwen2.5:1.5b | 47 % | — | 62 % / 88 % |

Decision: **qwen3.5:4b**. Smaller models are fast but unusable; the 9B is no more accurate.

End-to-end server timings (real Ollama, three scripted calls): turns without LLM p50 **3 ms**;
turns with LLM p50 **~700 ms**, p95 ~940 ms. In a standard call 4 of 6 caller turns need no LLM.

Ollama findings: `format` with a JSON Schema hangs 5 min with `think=false` and reasons >20k
tokens with `think=true`; `format="json"` + `think=false` works. Output tokens dominate latency
(~80 tok/s decode on this GPU); prompt prefix is KV-cached (~120 ms for 370 tokens).

## Budget

Target ≤ 1200 ms with LLM: VAD 550 + STT ~150–300 + server ~700 + network ~10 + TTS start ~50–150.
This lands around 1400–1700 ms for LLM turns and ~800 ms for deterministic turns. Next levers, in
order: shorter VAD window (450 ms) once STT is robust to it; streaming/partial interpretation on
interim STT; trimming the fused prompt; a faster decoder (quantisation, `num_predict`); server TTS
with lower start latency if the browser voice proves slow.

## Metrics contract

- Server (`GestorLlamadas` → `RegistroMetricas`): `stt_ms`, `interpretacion_ms`,
  `clasificacion_ms`, `llm_llamadas`, `grafo_ms`, `servidor_ms`, `origen_interpretacion`.
- Client (`POST /api/llamadas/{id}/metricas`): `vad_silencio_ms`, `red_ms`, `tts_inicio_ms`,
  `respuesta_ms`, `stt_cliente`.
- `GET /api/metricas` and `GET /api/llamadas/{id}/metricas` return p50/p95/max per stage and the
  count per rating. The console shows the same in the "Latencia" tab.

## Success Criteria

- p50 `respuesta_ms` ≤ 1200 ms on a standard call in the console; p95 ≤ 1500 ms.
- A caller who gives finca, vivienda, name and problem in one sentence is asked nothing twice.
- `repetir`, `humano`, `colgar`, `pregunta`, `fuera_ambito` work from any node without losing state.
- Every LLM turn is auditable: model, prompt version, origin (determinista/llm) and timing.
