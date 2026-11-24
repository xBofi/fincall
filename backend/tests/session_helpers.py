"""Helpers shared by the Spanish and the English suites (`tests/` and `tests/en/`)."""

from __future__ import annotations

from building_call.repos.memory import MemoryRepository

# Con largo real: por debajo del mínimo que exige la API es como no tener secreto
TEST_SECRET = "secreto-de-prueba-con-largo-suficiente-0123456789"


def make_session(repo: MemoryRepository, role: str = "owner") -> str:
    """Inserta un usuario y una sesión vigente en el repositorio en memoria; devuelve el token
    para la cookie `bc_session`. Síncrono a propósito: sirve también con `TestClient`."""
    from datetime import UTC, datetime, timedelta

    from building_call.auth import hash_session_token
    from building_call.domain.operations import User
    from building_call.repos.operational import AccessSession

    repo._operational()
    user = User(
        id=f"usr-{role}",
        username=str(role),
        role=role,
        active=True,
        password_hash="x",  # type: ignore[arg-type]
    )
    repo.users[user.id] = user
    token = f"token-de-prueba-{role}"
    now = datetime.now(UTC)
    repo.sessions[hash_session_token(token)] = AccessSession(
        f"ses-{role}", user.id, hash_session_token(token), now, now + timedelta(hours=1)
    )
    return token
