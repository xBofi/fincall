"""Calls from the web console through Twilio (Voice JavaScript SDK).

The console asks for a short-lived Access Token, the browser connects to Twilio, and Twilio
posts the call to the TwiML App's voice URL: the same `voice` webhook and Media Streams bridge as
a call to the phone number. The console opens the call first and passes its id as a custom
parameter, so it can follow the call live while Twilio carries the audio.

Access Token format: https://www.twilio.com/docs/iam/access-tokens
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

#: Identity prefix of the console's browser clients; Twilio sends it as `From=client:<identity>`
CLIENT_PREFIX = "console-"
TOKEN_TTL_S = 3600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def voice_access_token(
    account_sid: str,
    api_key_sid: str,
    api_key_secret: str,
    application_sid: str,
    identity: str,
    *,
    ttl_s: int = TOKEN_TTL_S,
    now: int | None = None,
) -> str:
    """HS256 JWT with a voice grant that can only call out to `application_sid`."""
    issued = int(time.time()) if now is None else now
    header = {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
    payload: dict[str, Any] = {
        "jti": f"{api_key_sid}-{issued}",
        "iss": api_key_sid,
        "sub": account_sid,
        "iat": issued,
        "nbf": issued,
        "exp": issued + ttl_s,
        "grants": {
            "identity": identity,
            "voice": {"outgoing": {"application_sid": application_sid}},
        },
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(api_key_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(signature)


def browser_ready_reason(settings: Any) -> str | None:
    """Why the console can't call through Twilio yet (no secrets in the message)."""
    if not (settings.twilio_api_key_sid and settings.twilio_api_key_secret):
        return "Missing BC_TWILIO_API_KEY_SID / BC_TWILIO_API_KEY_SECRET"
    if not settings.twilio_twiml_app_sid:
        return "Missing BC_TWILIO_TWIML_APP_SID (run building_call.telephony.configure)"
    return None


def console_identity(username: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in username)[:60]
    return CLIENT_PREFIX + safe
