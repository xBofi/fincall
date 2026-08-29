"""Primitivas de acceso local: hash de contraseñas, política y limitador de intentos."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from building_call.acceso import LimitadorIntentos
from building_call.auth import (
    hash_contrasena,
    validar_contrasena_nueva,
    validar_nombre_usuario,
    verificar_contrasena,
)


def test_hash_de_contrasena_usa_scrypt_con_sal_y_verifica() -> None:
    h1, h2 = hash_contrasena("una clave larga"), hash_contrasena("una clave larga")
    assert h1.startswith("scrypt$") and h1 != h2  # sal aleatoria
    assert verificar_contrasena("una clave larga", h1)
    assert not verificar_contrasena("otra", h1)
    assert not verificar_contrasena("una clave larga", "basura")


def test_politica_de_contrasena_y_nombre_de_usuario() -> None:
    assert validar_nombre_usuario("  Andreu.Bofi ") == "andreu.bofi"
    with pytest.raises(ValueError):
        validar_nombre_usuario("con espacios")
    with pytest.raises(ValueError, match="al menos"):
        validar_contrasena_nueva("corta", "admin")
    with pytest.raises(ValueError, match="nombre de usuario"):
        validar_contrasena_nueva("andreu.bofi", "andreu.bofi")
    validar_contrasena_nueva("frase-larga-y-privada", "admin")


def test_limitador_bloquea_tras_el_maximo_y_libera_al_pasar_la_ventana() -> None:
    lim = LimitadorIntentos(maximo=3, ventana_s=60)
    t0 = datetime.now(UTC)
    for _ in range(3):
        lim.fallo("u:admin", t0)
    assert lim.bloqueado("u:admin", t0)
    assert not lim.bloqueado("u:otro", t0)
    assert not lim.bloqueado("u:admin", t0 + timedelta(seconds=61))
