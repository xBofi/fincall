# Integrations: local run (2026-11-24)

Screenshots of a local n8n 2.49.5 (`docker compose --profile n8n up -d n8n`) receiving a simulated
Fincall call: the real LangGraph flow with the local LLM (Ollama `qwen3.5`), signed events, and
the `book_visit` tool. Reproduce with `backend/evals/e2e_n8n.py` (result in
`backend/evals/last_e2e_n8n.json`).

The action nodes (SMTP, Slack, Twilio, Google Calendar) had **fake local credentials**: they
fail on purpose (no external account was used), which shows that everything before them ran.

| Screenshot | What it shows |
|---|---|
| `n8n-book-visit-workflow.png` | The `book_visit` tool workflow: verify the HMAC, propose a slot, check Google Calendar, hold the slot, answer. |
| `n8n-book-visit-execution.png` | The tool call made during the call: signature verified, demo calendar branch, `{booked: true, slot_text, booking_id}` returned (39 ms inside n8n). |
| `n8n-ticket-to-maintainer-workflow.png` | `ticket.created` → e-mail and Slack message to the maintainer. |
| `n8n-ticket-execution.png` | The ticket event of the call: signature verified, deduplicated, message written; the e-mail node fails only on the fake SMTP. |
| `n8n-urgent-escalation-workflow.png` | Emergencies, high-urgency tickets and handoffs → SMS to the on-call coordinator. |
| `console-rule-ar-rtl.png`, `console-rule-ur-rtl.png` | The console in Arabic and Urdu (RTL) after the rebase on the multilingual branch: the last "what it doesn't do" item states the slot rule (never a firm time; with the n8n calendar on, a provisional slot pending the maintainer's confirmation), translated in all six languages (`sim.cannot.5`). Throwaway local console, memory repository. |
| `n8n-replay-dropped-execution.png` | The same event sent twice: the second run stops at **Skip retried events** (same `Idempotency-Key`), so nothing is sent twice. Test copy of the ticket workflow with the actions disabled. |

Measured in that run (the closing has since been reworded so it always says the visit is
provisional and pending the maintainer's confirmation, in every language: business rule of
2026-11-24, `notify_channel.visit_notice`):

- The caller heard: "Se lo comunico ahora a Fontanería Pérez. Según su agenda puede pasar el
  miércoles 25 entre las 9:00 y las 11:00; se lo dejo reservado y le llamará a este mismo número
  para confirmarlo."
- The same run in English (`evals/e2e_n8n.py --language en`, US demo building, after the
  rebase on the English port): "I'm passing it to Smith Plumbing now. Their calendar has an
  opening on Wednesday, November 25, between 9 and 11 AM; I've held it for you and they will call
  you back at this number to confirm it." (`backend/evals/last_e2e_n8n_en.json`).
- `book_visit` round trip from the backend: ~510 ms (twice), of which ~40 ms is the workflow
  itself; the rest is n8n's webhook and task-runner overhead. It is spent inside the caller's
  last turn, so the 1.5 s timeout stays.
- Events: `call.started`, `ticket.created` and `call.ended` delivered to both workflows on the
  first attempt (HTTP 200). Unsigned requests are rejected by **Verify signature**.
