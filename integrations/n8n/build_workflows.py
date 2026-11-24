"""Builds the example n8n workflows in this folder from readable JavaScript.

    python integrations/n8n/build_workflows.py

The JSON files are what you import in n8n; this script only exists so the Code nodes stay
reviewable (and identical across workflows) instead of living as escaped strings.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
NS = uuid.UUID("6f1c2a52-4d1e-4a8e-9f60-0d7c2b9c1f00")  # stable ids across rebuilds

# Verifies the Fincall signature exactly as backend/src/building_call/integrations/webhooks.py
# signs it: v1=HMAC-SHA256(secret, "<timestamp>.<canonical JSON body>"), 5-minute window.
VERIFY_JS = r"""
// Fincall signs every request: X-Fincall-Signature = v1=HMAC-SHA256(secret, "<ts>.<body>").
// The body is canonical JSON (sorted keys, compact), so it can be rebuilt from the parsed object.
// Needs NODE_FUNCTION_ALLOW_BUILTIN=crypto and FINCALL_WEBHOOK_SECRET in n8n's environment.
const crypto = require('crypto');
const secret = $env.FINCALL_WEBHOOK_SECRET;
if (!secret) throw new Error('FINCALL_WEBHOOK_SECRET is not set in n8n');

const canonical = (v) =>
  Array.isArray(v) ? '[' + v.map(canonical).join(',') + ']'
  : v !== null && typeof v === 'object'
    ? '{' + Object.keys(v).sort().map((k) => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}'
    : JSON.stringify(v);

const req = $input.first().json;
const h = req.headers || {};
const ts = Number(h['x-fincall-timestamp']);
if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > 300) {
  throw new Error('Missing or stale X-Fincall-Timestamp (replay?)');
}
const expected = 'v1=' + crypto.createHmac('sha256', secret).update(ts + '.' + canonical(req.body)).digest('hex');
const got = String(h['x-fincall-signature'] || '');
if (got.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(got), Buffer.from(expected))) {
  throw new Error('Invalid X-Fincall-Signature');
}
return [{ json: { ...req.body, idempotency_key: h['idempotency-key'] || req.body.id } }];
""".strip()


def _id(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def node(
    workflow: str,
    name: str,
    type_: str,
    version: float,
    position: tuple[int, int],
    parameters: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "parameters": parameters,
        "id": _id(f"{workflow}/{name}"),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": list(position),
        **extra,
    }


def webhook(workflow: str, path: str, respond_with_node: bool = False) -> dict[str, Any]:
    return node(
        workflow,
        "Fincall webhook",
        "n8n-nodes-base.webhook",
        2,
        (0, 300),
        {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "responseNode" if respond_with_node else "onReceived",
            "options": {},
        },
        webhookId=_id(f"{workflow}/webhook"),
    )


def code(workflow: str, name: str, position: tuple[int, int], js: str) -> dict[str, Any]:
    return node(workflow, name, "n8n-nodes-base.code", 2, position, {"jsCode": js.strip()})


def dedupe(workflow: str, position: tuple[int, int]) -> dict[str, Any]:
    """Idempotency: the same event (same Idempotency-Key) is processed once even if retried."""
    return node(
        workflow,
        "Skip retried events",
        "n8n-nodes-base.removeDuplicates",
        2,
        position,
        {
            "operation": "removeItemsSeenInPreviousExecutions",
            "logic": "removeItemsWithAlreadySeenKeyValues",
            "dedupeValue": "={{ $json.idempotency_key }}",
            "options": {},
        },
    )


def chain(*names: str) -> dict[str, Any]:
    connections: dict[str, Any] = {}
    for a, b in zip(names, names[1:], strict=False):
        connections.setdefault(a, {"main": [[]]})["main"][0].append(
            {"node": b, "type": "main", "index": 0}
        )
    return connections


def workflow(name: str, nodes: list[dict[str, Any]], connections: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1", "timezone": "Europe/Madrid"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
        "tags": [],
    }


# ---------------------------------------------------------------- 1. ticket → maintainer


def ticket_to_maintainer() -> dict[str, Any]:
    w = "ticket-to-maintainer"
    message_js = r"""
// One plain-text message for e-mail and Slack, in the call's language (`language` in the
// event: the building's market). Adding a language = one entry in L; unknown -> English.
const L = {
  es: { title: 'Nueva incidencia', urgency: 'urgencia', building: 'Finca', home: 'Vivienda',
        summary: 'Resumen', pending: 'Pendiente del administrador', noPro: 'sin profesional',
        assigned: 'Asignada a', call: 'Llamada' },
  en: { title: 'New maintenance request', urgency: 'urgency', building: 'Building', home: 'Unit',
        summary: 'Summary', pending: 'Waiting for the property manager', noPro: 'no contractor',
        assigned: 'Assigned to', call: 'Call' },
};
return $input.all().map(({ json: e }) => {
  const t = L[e.language] || L.en;
  const d = e.data || {};
  const lines = [
    `${t.title} ${d.incident_id} (${d.category}, ${t.urgency} ${d.urgency})`,
    `${t.building}: ${d.building || d.estate_id || '-'} · ${t.home}: ${d.apartment || '-'}`,
    `${t.summary}: ${d.summary || '-'}`,
    d.needs_administrator
      ? `${t.pending} (${d.review_reason || t.noPro})`
      : `${t.assigned}: ${d.specialist_name}`,
    `${t.call}: ${e.call_id} · ${e.created_at}`,
  ];
  return { json: { ...e, subject: `[Fincall] ${d.category} · ${d.urgency} · ${d.building || ''}`, text: lines.join('\n') } };
});
"""
    only_tickets = r"""
// Fincall sends every call event to this URL; this workflow only cares about new tickets.
return $input.all().filter((i) => i.json.type === 'ticket.created');
"""
    nodes = [
        webhook(w, "fincall-ticket"),
        code(w, "Verify signature", (220, 300), VERIFY_JS),
        code(w, "Only new tickets", (440, 300), only_tickets),
        dedupe(w, (660, 300)),
        code(w, "Write the message", (880, 300), message_js),
        node(
            w,
            "E-mail the maintainer",
            "n8n-nodes-base.emailSend",
            2.1,
            (1100, 200),
            {
                "fromEmail": "fincall@example.com",
                "toEmail": "mantenimiento@example.com",
                "subject": "={{ $json.subject }}",
                "emailFormat": "text",
                "text": "={{ $json.text }}",
                "options": {},
            },
        ),
        node(
            w,
            "Post to Slack",
            "n8n-nodes-base.slack",
            2.2,
            (1100, 400),
            {
                "select": "channel",
                "channelId": {"__rl": True, "value": "#mantenimiento", "mode": "name"},
                "text": "={{ $json.text }}",
                "otherOptions": {},
            },
        ),
    ]
    connections = chain(
        "Fincall webhook",
        "Verify signature",
        "Only new tickets",
        "Skip retried events",
        "Write the message",
    )
    connections["Write the message"] = {
        "main": [
            [
                {"node": "E-mail the maintainer", "type": "main", "index": 0},
                {"node": "Post to Slack", "type": "main", "index": 0},
            ]
        ]
    }
    return workflow("Fincall · ticket to maintainer (e-mail + Slack)", nodes, connections)


# ---------------------------------------------------------------- 2. urgent → SMS escalation


def urgent_escalation() -> dict[str, Any]:
    w = "urgent-escalation"
    urgent_js = r"""
// Escalate what cannot wait for the morning: emergencies, high-urgency tickets, and calls the
// assistant handed to a person. SMS text in the call's language; unknown -> English.
const L = {
  es: { emergency: 'EMERGENCIA', person: 'Vecino pide una persona', urgency: 'Urgencia' },
  en: { emergency: 'EMERGENCY', person: 'Caller asked for a person', urgency: 'Urgency' },
};
const urgent = (e) =>
  e.type === 'emergency.detected' ||
  e.type === 'handoff.requested' ||
  (e.type === 'ticket.created' && ['high', 'emergency'].includes(e.data?.urgency));
return $input.all().filter((i) => urgent(i.json)).map(({ json: e }) => {
  const t = L[e.language] || L.en;
  const d = e.data || {};
  const what = {
    'emergency.detected': t.emergency,
    'handoff.requested': t.person,
    'ticket.created': `${t.urgency} ${d.urgency}`,
  }[e.type];
  // SMS: short, no personal data beyond the building and the apartment
  const sms = `[Fincall] ${what}: ${d.building || d.estate_id || '-'} ${d.apartment || ''}. ${d.summary || d.reason || d.request || ''}`.slice(0, 300);
  return { json: { ...e, sms } };
});
"""
    nodes = [
        webhook(w, "fincall-urgent"),
        code(w, "Verify signature", (220, 300), VERIFY_JS),
        code(w, "Only urgent events", (440, 300), urgent_js),
        dedupe(w, (660, 300)),
        node(
            w,
            "SMS the on-call coordinator",
            "n8n-nodes-base.twilio",
            1,
            (880, 300),
            {
                "from": "+34000000000",
                "to": "+34000000001",
                "message": "={{ $json.sms }}",
                "options": {},
            },
        ),
    ]
    connections = chain(
        "Fincall webhook",
        "Verify signature",
        "Only urgent events",
        "Skip retried events",
        "SMS the on-call coordinator",
    )
    return workflow("Fincall · urgent ticket to SMS escalation", nodes, connections)


# ---------------------------------------------------------------- 3. book a visit (tool)


def book_visit() -> dict[str, Any]:
    w = "book-visit"
    propose_js = r"""
// Proposes the next working day, 9:00-11:00, and the sentence the assistant will say, in the
// call's language (`language` in the request). Fincall validates slot_text before speaking it
// (one short line of plain text). Adding a language = one entry in LANGS; unknown -> English.
const LANGS = {
  es: { locale: 'es-ES', say: (day) => `el ${day}, entre las 9:00 y las 11:00` },
  en: { locale: 'en-US', say: (day) => `on ${day}, between 9 and 11 AM` },
};
const req = $input.first().json;
const lang = LANGS[req.language] || LANGS.en;
const d = new Date();
do { d.setDate(d.getDate() + 1); } while (d.getDay() === 0 || d.getDay() === 6);
const ymd = d.toISOString().slice(0, 10);
const day = new Intl.DateTimeFormat(lang.locale, { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
return [{ json: {
  ...req,
  start: `${ymd}T09:00:00`,
  end: `${ymd}T11:00:00`,
  slot_text: lang.say(day),
  demo: $env.FINCALL_CALENDAR_DEMO === 'true',
} }];
"""
    nodes = [
        webhook(w, "fincall-book-visit", respond_with_node=True),
        code(w, "Verify signature", (220, 300), VERIFY_JS),
        code(w, "Propose a slot", (440, 300), propose_js),
        node(
            w,
            "Demo calendar?",
            "n8n-nodes-base.if",
            2,
            (660, 300),
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                    "conditions": [
                        {
                            "id": _id(f"{w}/demo-cond"),
                            "leftValue": "={{ $json.demo }}",
                            "rightValue": True,
                            "operator": {
                                "type": "boolean",
                                "operation": "true",
                                "singleValue": True,
                            },
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        ),
        node(
            w,
            "Is the maintainer free?",
            "n8n-nodes-base.googleCalendar",
            1.3,
            (880, 420),
            {
                "resource": "calendar",
                "calendar": {"__rl": True, "value": "primary", "mode": "list"},
                "timeMin": "={{ $json.start }}",
                "timeMax": "={{ $json.end }}",
                "options": {"outputFormat": "availability", "timezone": "Europe/Madrid"},
            },
        ),
        node(
            w,
            "Free?",
            "n8n-nodes-base.if",
            2,
            (1100, 420),
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                    "conditions": [
                        {
                            "id": _id(f"{w}/free-cond"),
                            "leftValue": "={{ $json.available }}",
                            "rightValue": True,
                            "operator": {
                                "type": "boolean",
                                "operation": "true",
                                "singleValue": True,
                            },
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        ),
        node(
            w,
            "Hold the slot",
            "n8n-nodes-base.googleCalendar",
            1.3,
            (1320, 340),
            {
                "calendar": {"__rl": True, "value": "primary", "mode": "list"},
                "start": "={{ $('Propose a slot').item.json.start }}",
                "end": "={{ $('Propose a slot').item.json.end }}",
                "additionalFields": {
                    "summary": "=Visita (provisional) · {{ $('Propose a slot').item.json.category }} · {{ $('Propose a slot').item.json.building }} {{ $('Propose a slot').item.json.apartment }}",
                    "description": "=Incidencia {{ $('Propose a slot').item.json.incident_id }}\n{{ $('Propose a slot').item.json.summary }}",
                },
            },
        ),
        node(
            w,
            "Answer: booked",
            "n8n-nodes-base.respondToWebhook",
            1.1,
            (1540, 340),
            {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify({ booked: true, slot_text: $('Propose a slot').item.json.slot_text, booking_id: $json.id }) }}",
                "options": {},
            },
        ),
        node(
            w,
            "Answer: no slot",
            "n8n-nodes-base.respondToWebhook",
            1.1,
            (1320, 520),
            {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify({ booked: false }) }}",
                "options": {},
            },
        ),
        node(
            w,
            "Answer: demo booking",
            "n8n-nodes-base.respondToWebhook",
            1.1,
            (880, 180),
            {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify({ booked: true, slot_text: $json.slot_text, booking_id: 'demo-' + $json.incident_id }) }}",
                "options": {},
            },
        ),
    ]
    connections = chain("Fincall webhook", "Verify signature", "Propose a slot", "Demo calendar?")
    connections["Demo calendar?"] = {
        "main": [
            [{"node": "Answer: demo booking", "type": "main", "index": 0}],
            [{"node": "Is the maintainer free?", "type": "main", "index": 0}],
        ]
    }
    connections["Is the maintainer free?"] = {
        "main": [[{"node": "Free?", "type": "main", "index": 0}]]
    }
    connections["Free?"] = {
        "main": [
            [{"node": "Hold the slot", "type": "main", "index": 0}],
            [{"node": "Answer: no slot", "type": "main", "index": 0}],
        ]
    }
    connections["Hold the slot"] = {
        "main": [[{"node": "Answer: booked", "type": "main", "index": 0}]]
    }
    return workflow("Fincall · book a technician visit (Google Calendar tool)", nodes, connections)


def main() -> None:
    for filename, build in (
        ("ticket-to-maintainer.json", ticket_to_maintainer),
        ("urgent-escalation.json", urgent_escalation),
        ("book-visit-google-calendar.json", book_visit),
    ):
        data = build()
        (HERE / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"{filename}: {len(data['nodes'])} nodes")


if __name__ == "__main__":
    main()
