from __future__ import annotations

import pytest

from building_call.auth import usuario_desde_google
from building_call.domain.operaciones import Rol


def test_google_owner_es_autorizado_por_lista_explicita() -> None:
    usuario = usuario_desde_google(
        {"sub": "google-123", "email": "dueno@example.test", "email_verified": True},
        permitidos={"dueno@example.test"},
        owners={"dueno@example.test"},
        administradores=set(),
        calidad=set(),
    )
    assert usuario.rol is Rol.owner
    assert usuario.google_sub == "google-123"


def test_google_rechaza_cuentas_fuera_de_la_lista() -> None:
    with pytest.raises(PermissionError, match="no autorizada"):
        usuario_desde_google(
            {"sub": "google-123", "email": "ajeno@example.test", "email_verified": True},
            permitidos={"dueno@example.test"},
            owners={"dueno@example.test"},
            administradores=set(),
            calidad=set(),
        )
