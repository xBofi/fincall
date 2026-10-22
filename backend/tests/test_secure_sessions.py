from building_call.auth import hash_session_token


def test_session_token_hash_is_deterministic_and_never_plaintext() -> None:
    token = "secreto-de-sesion"
    digest = hash_session_token(token)
    assert digest == hash_session_token(token)
    assert digest != token
    assert len(digest) == 64
