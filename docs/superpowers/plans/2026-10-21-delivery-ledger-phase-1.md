# Delivery Ledger, Phase 1: Pieces and Ledger in the Graph

Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md` (§1-§3). Transport
stays as it is (browser, WebSocket, Twilio). Phase 2 (LiveKit) only has to send a more exact
`texto_entregado`.

## Decisions taken while planning

- **Pieces are sentences of the turn text** (`" ".join(mensajes_bot)`, exactly what the browser
  and Twilio speak), so a cut measured in characters maps onto them. Importance:
  - a sentence with `¿`/`?` → `pregunta`;
  - a sentence that came from a template marked `aviso=True` → `aviso` (the greeting notice and
    the closes that say the next step);
  - three words or fewer → `cortesia`;
  - anything else → `dato`.
  Templates mark avisos, not sentences: the node records the rendered aviso text in
  `avisos_turno` and the splitter matches sentences against it. A paraphrased reply (local
  redaction) loses the mark and counts as `dato`; redaction is off with API LLMs.
- **A partial piece with less than 60 % of its words heard counts as not heard.**
- **A bare yes/no is ignored** (the full message is said again) when a `dato` was not heard or
  the `pregunta` was not heard at all. If every `dato` was heard and the question was only cut
  ("¿Es corr—" "Sí"), the yes counts: the caller is answering over the end of the question.
- **No ledger report means everything was heard**, as today.
- **No measured position, no pieces:** Twilio cuts and reports a fixed 0.5 ratio with no text.
  Deciding on an invented position ignored a legitimate "sí, claro" over the question, so
  without `texto_entregado` no piece is evaluated and the next turn behaves as before. LiveKit
  (phase 2) and the browser send the text.
- **The prompt line is not enough on its own:** with the heard text in the prompt, Gemini still
  returned `confirma=true` for "Sí, sí, y además sale agua por el techo" over an unheard
  summary. The graph drops that `confirma` deterministically and keeps the data.
- **The close of a finished call is out of phase 1:** the transport hangs up on
  `finalizada`. Phase 2: the agent replays the close's aviso pieces before hanging up when the
  close was interrupted.

## Tasks (TDD, fake LLM)

1. `graph/ledger.py`: `trocear(texto, avisos)` and `evaluar(piezas, corte)` + queries
   (`avisos_no_oidos`, `confirmacion_no_valida`, `texto_oido_si_cortado`). Unit tests.
2. `Plantilla.aviso` and the marks; `_anotar_plantilla` collects `avisos_turno`.
3. `GestorLlamadas`: `piezas_bot` per turn; `registrar_entrega` stores the evaluated pieces;
   avisos not heard in the previous turn are put in front of the next reply.
4. `_turno`: a bare yes/no after an unheard summary or question repeats the message.
5. `interpretar(..., oido=)`: one line with what was heard, only when it was cut; also on the
   speculative call over partial transcripts.
6. Gold case for the prompt line; Vertex eval before/after; full suite, ruff.
