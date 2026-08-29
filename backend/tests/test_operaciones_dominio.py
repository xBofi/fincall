from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from building_call.domain.operaciones import PistaAudio, Rol, Usuario


def test_roles_are_closed() -> None:
    assert Rol("calidad") is Rol.calidad
    with pytest.raises(ValueError):
        Rol("operador")


def test_audio_expiry_must_follow_creation() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        PistaAudio(
            llamada_id="llam-1",
            canal="cliente",
            creada_en=now,
            expira_en=now,
        )


def test_user_never_exposes_password_hash() -> None:
    user = Usuario(
        id="user-1", nombre_usuario="calidad", rol=Rol.calidad, activo=True, hash_contrasena="h"
    )
    assert user.rol is Rol.calidad
    assert "hash_contrasena" not in user.publico()
