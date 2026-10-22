from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from building_call.domain.operations import AudioTrack, Role, User


def test_roles_are_closed() -> None:
    assert Role("quality") is Role.quality
    with pytest.raises(ValueError):
        Role("operador")


def test_audio_expiry_must_follow_creation() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        AudioTrack(
            call_id="llam-1",
            channel="client",
            created_at=now,
            expires_at=now,
        )


def test_user_never_exposes_password_hash() -> None:
    user = User(
        id="user-1", username="quality", role=Role.quality, active=True, password_hash="h"
    )
    assert user.role is Role.quality
    assert "password_hash" not in user.public()
