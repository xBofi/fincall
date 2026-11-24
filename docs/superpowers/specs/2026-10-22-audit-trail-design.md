# Audit Trail, Personal Data and Call Erasure

## Goal

Pass an audit (RGPD art. 5.2, 17 and 32). From the database alone we must be able to answer:

1. **Changes:** who inserted, updated or deleted each row, when, and what changed.
2. **Access:** who read which record, when they opened it and when they stopped looking at it,
   and every playback or download of a file.
3. **Erasure:** a call can be erased on request, with everything derived from it, leaving a
   proof that it was erased but nothing of its content.

Product decision of 2026-10-22:

- Only **who** is recorded: no IP, no browser.
- **Nobody sees the audit** in the application: no screen and no query API. The operator asks
  Claude, who reads the database (`building_call.audit.report`).
- Retention of the audit log: **two years** (Confirmed).
- **Personal data is per field:** a field is personal if it can end up identifying a person
  (phone number, e-mail, ID number, address, voice, free text the caller dictated). A name on
  its own is not, but the **caller's name is no longer stored at all**.
- **Erasure** of a call removes its content and everything derived from it; its incidents
  stay, anonymised, pointing to "deleted call". Only the owner can erase.
- Configuration lives in **static class attributes**, not in a JSON blob.
- Code and schema are in English (see the English refactor); the product stays Spanish.

## 1. Change log: row-level triggers

The standard pattern for a tamper-resistant change log in PostgreSQL (the wiki's "audit
trigger", `supa_audit`): an `AFTER INSERT OR UPDATE OR DELETE ... FOR EACH ROW` trigger writes
one row per change into the append-only `audit_changes`. It catches the ORM, Core
`update()`/`delete()`, migrations and `psql`.

The actor travels with each transaction: a SQLAlchemy `after_begin` hook runs
`set_config('fincall.actor', ..., true)` (transaction-local, so pooled connections never leak
it). Without one, the trigger records `db:<role>`.

| `audit_changes` | |
|---|---|
| `table_name`, `record_id`, `operation` | `insert` · `update` · `delete` · `erase` |
| `actor`, `origin`, `request_id` | who, from which channel (console, agent, telephony, system) |
| `occurred_at`, `transaction_id` | |
| `before`, `after` | only the columns that changed; personal and secret fields as a fingerprint |

## 2. The base class every table inherits from

```python
class AuditedTable(Base):
    __abstract__ = True
    AUDIT_CHANGES: ClassVar[bool] = True              # write audit_changes rows
    PERSONAL_FIELDS: ClassVar[tuple[str, ...]] = ()   # can identify a person
    SECRET_FIELDS: ClassVar[tuple[str, ...]] = ()     # hashes, tokens
    IGNORED_FIELDS: ClassVar[tuple[str, ...]] = ()    # a change only here is not logged
    audit_created_at, audit_created_by, audit_updated_at, audit_updated_by  # set by a trigger
```

- Every business table inherits from `AuditedTable`; the audit tables inherit from `Base`.
- The stamps carry an `audit_` prefix: several tables already have their own `created_at` or
  `updated_at` that the application manages (for instance the purge of live call states).
- Personal and secret fields are logged as `{"fingerprint": "<16 hex of sha256>"}`: the log
  never becomes a copy of personal data that outlives the original.
- A record is **personal data** when its table has at least one personal field: reading it is
  logged in `audit_access`.
- Trigger arguments are generated from the class attributes (`audit/install.py`); a test fails
  if the installed triggers and the classes disagree.

Personal fields (first version):

| Table | Personal fields |
|---|---|
| calls | `caller_phone`, `transcript` |
| call_turns, call_decisions | `data` (what the caller said, extracted address) |
| call_states | `state` (the whole live call) |
| audio_tracks | the recording itself (the row points to it): `object_key` |
| incidents | `description` (dictated by the caller) |
| bank_cases | `utterance`, `heard_text` |
| users | `email`, `subject` |
| specialists | `target` (may be a person's phone or e-mail) |

Homes, estates and equipment hold no personal field: an estate's contact is the managing
company's, and a home alone does not say who lives there.

## 3. Access log

`audit_access`, one row per access: `action` (`read`, `list`, `view`, `play`, `download`),
`resource`, `resource_id`, `actor`, `origin`, `request_id`, `started_at`, `ended_at`,
`last_heartbeat_at`, `detail`.

- `read`/`list`: the API served a record or a list (an instant). Every endpoint that returns a
  record of a personal-data table calls `access.log.read(CallSQL, id)` (or `list`, `play`,
  `download`). If the log cannot be written, the request fails.
- `view`: the record was on screen. The console opens a view, sends a heartbeat every 30 s and
  closes it on unmount, hidden tab or page close (`sendBeacon`). A view with no heartbeat for
  two minutes ends at its last heartbeat.
- Append-only: a trigger rejects any change except closing or extending an open view, and the
  scrubbing of an erased call (§5).

## 4. The caller's name is not stored

The summary does not include a name, nothing greets the caller by it, and
`calls.declared_name` is dropped. Migration: names already stored are removed (the column, the
`name` key of stored interpretations, the live states and the `name_registered` decisions).
The transcript keeps what the caller said: it is personal data, protected and erasable.

Implementation decisions (2026-10-22):

- The model's prompt still has the `nombre` key, as a sink: the model puts "soy Ana" there
  instead of inside `problema`. The graph discards it the moment it arrives (not in the state,
  not in the turn record). Prompts stay byte-identical, so the measured accuracy does not move.
- Test-bank cases pseudonymise the name that quality writes as the expected `name` of that
  turn (there is no stored name to take it from anymore).
- The `{who}` placeholder of `confirm_summary.summary` is still passed, always empty, so a
  published override that uses it does not break. `collect_request.greeting_with_name` is gone
  (and its published overrides, in migration 0012).

## 5. Erasure of a call

`POST /api/operations/calls/{id}/erase` (owner only; the console asks for confirmation):

1. Deleted: the call's turns, decisions, audio tracks and their files, test-bank cases and live
   state, and the call row itself.
2. Its incidents stay, anonymised: they point to the sentinel call `deleted-call`, their
   description, suggestion and history notes are emptied. Category, urgency, state and the
   professional stay (the work record).
3. The audit log keeps that something happened, not what: rows about the call and its
   children get `record_id = 'deleted-call'` and their values are replaced with
   `{"erased": true}`; access rows get `resource_id = 'deleted-call'`.
4. One `erase` row in `audit_changes` with the actor, the time and the fingerprint of the call
   id, so a later request ("did you erase my call?") can be answered without keeping the id.

All of it runs in one transaction with `fincall.erasure = 'on'`, the only mode in which the
audit tables accept those updates. Anywhere a reference to the sentinel is shown, the console
reads "Llamada eliminada".

## 6. Retention

`BC_AUDIT_RETENTION_DAYS = 730` (Confirmed). The purge sets `fincall.purge = 'on'`, the only
mode in which the audit tables accept deletes. It runs with the hourly retention job.

## Tests

Unit: base class and attributes, generated trigger SQL, context propagation, access log
(memory), erasure (memory). Integration with PostgreSQL (`BC_TEST_DATABASE_URL`): triggers,
fingerprints, ignored fields, append-only, purge, erasure end to end, migration up and down,
installed triggers match the classes, a console change logged with the person's actor.

## Reading the log

There is no audit screen nor query API (only `/api/audit/views*`, which writes). Claude reads
it when The operator asks, with `uv run python -m building_call.audit.report` (`changes`, `access`,
`record <table> <id>`, `erased <call_id>`, `fingerprint <value>`); each read is itself logged
as `download` under actor `claude:report`.
