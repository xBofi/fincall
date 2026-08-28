from building_call.repos.modelos_sql import (
    AuditoriaEventoSQL,
    LlamadaDecisionSQL,
    LlamadaTurnoSQL,
    PistaAudioSQL,
    SesionSQL,
    UsuarioSQL,
)


def test_operational_models_have_stable_table_names() -> None:
    assert UsuarioSQL.__tablename__ == "usuarios"
    assert SesionSQL.__tablename__ == "sesiones"
    assert LlamadaTurnoSQL.__tablename__ == "llamada_turnos"
    assert LlamadaDecisionSQL.__tablename__ == "llamada_decisiones"
    assert PistaAudioSQL.__tablename__ == "pistas_audio"
    assert AuditoriaEventoSQL.__tablename__ == "auditoria_eventos"
