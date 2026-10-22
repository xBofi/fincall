from building_call.repos.sql_models import (
    AudioTrackSQL,
    AuditEventSQL,
    BankCaseSQL,
    CallDecisionSQL,
    CallTurnSQL,
    SessionSQL,
    UserSQL,
)


def test_operational_models_have_stable_table_names() -> None:
    assert UserSQL.__tablename__ == "users"
    assert SessionSQL.__tablename__ == "sessions"
    assert CallTurnSQL.__tablename__ == "call_turns"
    assert CallDecisionSQL.__tablename__ == "call_decisions"
    assert AudioTrackSQL.__tablename__ == "audio_tracks"
    assert AuditEventSQL.__tablename__ == "audit_events"
    assert BankCaseSQL.__tablename__ == "bank_cases"
