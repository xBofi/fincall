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
| `n8n-replay-dropped-execution.png` | The same event sent twice: the second run stops at **Skip retried events** (same `Idempotency-Key`), so nothing is sent twice. Test copy of the ticket workflow with the actions disabled. |

Measured in that run:

- The caller heard: "Se lo comunico ahora a Fontanería Pérez. Según su agenda puede pasar el
  miércoles 25 entre las 9:00 y las 11:00; se lo dejo reservado y le llamará a este mismo número
  para confirmarlo."
- `book_visit` round trip from the backend: ~510 ms (twice), of which ~40 ms is the workflow
  itself; the rest is n8n's webhook and task-runner overhead. It is spent inside the caller's
  last turn, so the 1.5 s timeout stays.
- Events: `call.started`, `ticket.created` and `call.ended` delivered to both workflows on the
  first attempt (HTTP 200). Unsigned requests are rejected by **Verify signature**.
