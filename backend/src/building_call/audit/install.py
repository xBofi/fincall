"""SQL for the audit triggers, generated from each table's static attributes.

Standard PostgreSQL change log (the wiki's "audit trigger", `supa_audit`): a row trigger writes
every insert, update and delete to `audit_changes`. It catches the ORM, SQLAlchemy Core
`update()`/`delete()`, migrations and anyone using `psql`. The application sets the actor on
every transaction (`audit/session.py`); without it the trigger records `db:<role>`.

Per table, two triggers with the same arguments (primary key, fingerprinted fields, ignored
fields):
- `audit_stamp` (BEFORE): writes `audit_created_*` and `audit_updated_*`.
- `audit_record` (AFTER): one row with only what changed; personal and secret fields as a
  fingerprint.

Modes, set per transaction:
- `fincall.purge = 'on'`: the retention job may delete from the audit tables.
- `fincall.erasure = 'on'`: erasing a call. Changes are logged without values and under the
  sentinel `deleted-call`, and the audit tables accept having that call's rows emptied.

A migration that changes a static attribute calls `install_sql()` again; otherwise the
integration test that compares installed triggers with the classes fails.
"""

from __future__ import annotations

from building_call.repos.sql_models import AuditedTable, Base

STAMPS = ("audit_created_at", "audit_created_by", "audit_updated_at", "audit_updated_by")
#: Append-only tables guarded by `fincall_audit_immutable`
IMMUTABLE_TABLES = ("audit_changes", "audit_access")
#: What an erased call turns into wherever it was referenced
DELETED_CALL = "deleted-call"

_STAMPS_SQL = "ARRAY['audit_created_at', 'audit_created_by', 'audit_updated_at', 'audit_updated_by']"
_ERASED = "'{\"erased\": true}'::jsonb"

FUNCTIONS = [
    # Who is acting: what the application set for the transaction, or the database role
    """
CREATE OR REPLACE FUNCTION fincall_audit_actor() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('fincall.actor', true), ''), 'db:' || current_user)
$$""",
    # Fingerprint of a value: the first 16 hex of the sha256 of its JSON text. Lets anyone check
    # a known value without the log keeping the value
    """
CREATE OR REPLACE FUNCTION fincall_audit_fingerprint(value jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN value IS NULL OR value = 'null'::jsonb THEN value
    ELSE jsonb_build_object(
      'fingerprint', left(encode(sha256(convert_to(value::text, 'UTF8')), 'hex'), 16))
  END
$$""",
    f"""
CREATE OR REPLACE FUNCTION fincall_audit_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor text := fincall_audit_actor();
  ignored text[] := coalesce(string_to_array(nullif(TG_ARGV[2], ''), ','), '{{}}');
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW.audit_created_at := clock_timestamp();
    NEW.audit_created_by := actor;
    NEW.audit_updated_at := NEW.audit_created_at;
    NEW.audit_updated_by := actor;
    RETURN NEW;
  END IF;
  NEW.audit_created_at := OLD.audit_created_at;
  NEW.audit_created_by := OLD.audit_created_by;
  IF (to_jsonb(NEW) - ignored - {_STAMPS_SQL})
     IS DISTINCT FROM (to_jsonb(OLD) - ignored - {_STAMPS_SQL}) THEN
    NEW.audit_updated_at := clock_timestamp();
    NEW.audit_updated_by := actor;
  ELSE
    NEW.audit_updated_at := OLD.audit_updated_at;
    NEW.audit_updated_by := OLD.audit_updated_by;
  END IF;
  RETURN NEW;
END
$$""",
    f"""
CREATE OR REPLACE FUNCTION fincall_audit_record() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  pk text := TG_ARGV[0];
  fingerprinted text[] := coalesce(string_to_array(nullif(TG_ARGV[1], ''), ','), '{{}}');
  ignored text[] := coalesce(string_to_array(nullif(TG_ARGV[2], ''), ','), '{{}}');
  stamps text[] := {_STAMPS_SQL};
  erasing boolean := current_setting('fincall.erasure', true) = 'on';
  old_row jsonb;
  new_row jsonb;
  before_values jsonb;
  after_values jsonb;
  operation text;
  record text;
  k text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    operation := 'insert';
    new_row := to_jsonb(NEW);
    after_values := new_row - stamps;
  ELSIF TG_OP = 'DELETE' THEN
    operation := 'delete';
    old_row := to_jsonb(OLD);
    before_values := old_row;
  ELSE
    operation := 'update';
    old_row := to_jsonb(OLD);
    new_row := to_jsonb(NEW);
    before_values := '{{}}'::jsonb;
    after_values := '{{}}'::jsonb;
    FOR k IN SELECT jsonb_object_keys(new_row) LOOP
      CONTINUE WHEN k = ANY(ignored) OR k = ANY(stamps);
      IF new_row -> k IS DISTINCT FROM old_row -> k THEN
        before_values := before_values || jsonb_build_object(k, old_row -> k);
        after_values := after_values || jsonb_build_object(k, new_row -> k);
      END IF;
    END LOOP;
    IF after_values = '{{}}'::jsonb THEN
      RETURN NULL;
    END IF;
  END IF;
  record := coalesce(new_row ->> pk, old_row ->> pk);
  IF erasing THEN
    -- Erasing a call: that it happened, by whom and when; not what, nor of which call
    record := '{DELETED_CALL}';
    before_values := CASE WHEN before_values IS NULL THEN NULL ELSE {_ERASED} END;
    after_values := CASE WHEN after_values IS NULL THEN NULL ELSE {_ERASED} END;
  ELSE
    FOREACH k IN ARRAY fingerprinted LOOP
      IF before_values ? k THEN
        before_values := jsonb_set(before_values, ARRAY[k], fincall_audit_fingerprint(before_values -> k));
      END IF;
      IF after_values ? k THEN
        after_values := jsonb_set(after_values, ARRAY[k], fincall_audit_fingerprint(after_values -> k));
      END IF;
    END LOOP;
  END IF;
  INSERT INTO audit_changes (
    occurred_at, table_name, record_id, operation, actor, origin, request_id, transaction_id,
    before, after
  ) VALUES (
    clock_timestamp(), TG_TABLE_NAME, record, operation, fincall_audit_actor(),
    nullif(current_setting('fincall.origin', true), ''),
    nullif(current_setting('fincall.request', true), ''),
    txid_current(), before_values, after_values
  );
  RETURN NULL;
END
$$""",
    # Append-only. Exceptions: the retention purge, extending or closing an open view, and
    # emptying an erased call's rows (and its incidents' reference to it)
    f"""
CREATE OR REPLACE FUNCTION fincall_audit_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('fincall.purge', true) = 'on' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'UPDATE' AND current_setting('fincall.erasure', true) = 'on' THEN
    -- Nested: PL/pgSQL does not short-circuit AND, and the columns differ per table
    IF TG_TABLE_NAME = 'audit_changes' THEN
      IF NEW.record_id = '{DELETED_CALL}'
         AND NEW.before IS NOT DISTINCT FROM (CASE WHEN OLD.before IS NULL THEN NULL ELSE {_ERASED} END)
         AND NEW.after IS NOT DISTINCT FROM (CASE WHEN OLD.after IS NULL THEN NULL ELSE {_ERASED} END)
         AND (to_jsonb(NEW) - ARRAY['record_id', 'before', 'after'])
             = (to_jsonb(OLD) - ARRAY['record_id', 'before', 'after'])
      THEN
        RETURN NEW;
      END IF;
      -- A row of an incident that stays: it only loses the reference to the erased call
      IF NEW.record_id = OLD.record_id AND OLD.table_name = 'incidents'
         AND NEW.before IS NOT DISTINCT FROM (CASE WHEN OLD.before ? 'call_id'
             THEN jsonb_set(OLD.before, '{{call_id}}', '"{DELETED_CALL}"') ELSE OLD.before END)
         AND NEW.after IS NOT DISTINCT FROM (CASE WHEN OLD.after ? 'call_id'
             THEN jsonb_set(OLD.after, '{{call_id}}', '"{DELETED_CALL}"') ELSE OLD.after END)
         AND (to_jsonb(NEW) - ARRAY['before', 'after'])
             = (to_jsonb(OLD) - ARRAY['before', 'after'])
      THEN
        RETURN NEW;
      END IF;
    ELSIF TG_TABLE_NAME = 'audit_access' THEN
      IF NEW.resource_id = '{DELETED_CALL}' AND NEW.detail = '{{}}'::jsonb
         AND (to_jsonb(NEW) - ARRAY['resource_id', 'detail'])
             = (to_jsonb(OLD) - ARRAY['resource_id', 'detail'])
      THEN
        RETURN NEW;
      END IF;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'audit_access' THEN
    -- An open view: heartbeats, closing it and, on close, the `close` key of `detail`
    IF OLD.ended_at IS NULL
       AND (to_jsonb(NEW) - ARRAY['ended_at', 'last_heartbeat_at', 'detail'])
           = (to_jsonb(OLD) - ARRAY['ended_at', 'last_heartbeat_at', 'detail'])
       AND NOT OLD.detail ? 'close'
       AND NEW.detail - 'close' = OLD.detail
       AND (NEW.detail ? 'close') <= (NEW.ended_at IS NOT NULL)
       AND coalesce(NEW.last_heartbeat_at, OLD.started_at)
           >= coalesce(OLD.last_heartbeat_at, OLD.started_at)
    THEN
      RETURN NEW;
    END IF;
  END IF;
  RAISE EXCEPTION 'The audit log is append-only (% on %)', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END
$$""",
]


def audited_tables() -> list[type[AuditedTable]]:
    """Every table that inherits from `AuditedTable`, by name."""
    classes = [m.class_ for m in Base.registry.mappers if issubclass(m.class_, AuditedTable)]
    return sorted(classes, key=lambda c: c.__tablename__)


def arguments(cls: type[AuditedTable]) -> tuple[str, str, str]:
    """(primary key, fingerprinted fields, ignored fields) as the trigger receives them."""
    (pk,) = cls.__table__.primary_key.columns  # type: ignore[attr-defined]
    fingerprinted = (*cls.PERSONAL_FIELDS, *cls.SECRET_FIELDS)
    return pk.name, ",".join(fingerprinted), ",".join(cls.IGNORED_FIELDS)


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def trigger_sql(cls: type[AuditedTable]) -> list[str]:
    table = cls.__tablename__
    args = ", ".join(_literal(a) for a in arguments(cls))
    statements = [
        f"CREATE OR REPLACE TRIGGER audit_stamp BEFORE INSERT OR UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION fincall_audit_stamp({args})"
    ]
    if cls.AUDIT_CHANGES:
        statements.append(
            f"CREATE OR REPLACE TRIGGER audit_record AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION fincall_audit_record({args})"
        )
    else:
        statements.append(f"DROP TRIGGER IF EXISTS audit_record ON {table}")
    return statements


def install_sql() -> list[str]:
    """Functions, the triggers of every audited table and the guard of the log. Idempotent."""
    statements = list(FUNCTIONS)
    for cls in audited_tables():
        statements += trigger_sql(cls)
    for table in IMMUTABLE_TABLES:
        statements += [
            f"CREATE OR REPLACE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION fincall_audit_immutable()",
            f"CREATE OR REPLACE TRIGGER audit_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION fincall_audit_immutable()",
        ]
    return statements


def uninstall_sql() -> list[str]:
    statements = []
    for table in IMMUTABLE_TABLES:
        statements += [
            f"DROP TRIGGER IF EXISTS audit_immutable ON {table}",
            f"DROP TRIGGER IF EXISTS audit_no_truncate ON {table}",
        ]
    for cls in audited_tables():
        table = cls.__tablename__
        statements += [
            f"DROP TRIGGER IF EXISTS audit_record ON {table}",
            f"DROP TRIGGER IF EXISTS audit_stamp ON {table}",
        ]
    statements += [
        f"DROP FUNCTION IF EXISTS {f}"
        for f in (
            "fincall_audit_record()",
            "fincall_audit_stamp()",
            "fincall_audit_immutable()",
            "fincall_audit_fingerprint(jsonb)",
            "fincall_audit_actor()",
        )
    ]
    return statements


#: What is installed: (table, trigger, arguments). Used by the consistency test
INSTALLED_TRIGGERS_SQL = """
SELECT c.relname AS table_name, t.tgname AS trigger_name,
       encode(t.tgargs, 'escape') AS arguments
FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
WHERE NOT t.tgisinternal AND t.tgname LIKE 'audit_%'
"""
