"""Deja el número de Twilio apuntando a esta API. Uso:

    cd backend
    uv run python -m building_call.telephony.configure            # comprueba y configura
    uv run python -m building_call.telephony.configure --comprobar  # solo comprueba

Lee `backend/.env`: BC_TELEPHONY=twilio, BC_PUBLIC_URL, BC_TWILIO_ACCOUNT_SID, BC_TWILIO_AUTH_TOKEN,
BC_TWILIO_NUMBER (E.164). Comprueba que la URL pública responde, que hay STT y TTS, y fija en el
número el webhook de voz (POST) y el de estado de llamada. Idempotente. No imprime secretos.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import httpx

from building_call.telephony.twilio import CALL_STATUS_PATH, HEALTH_PATH, VOICE_PATH

API_TWILIO = "https://api.twilio.com/2010-04-01"


def not_ready_reason(settings: Any, tts_available: bool, stt_available: bool) -> str | None:
    """Explica (sin secretos) por qué la telefonía aún no puede atender llamadas."""
    if settings.telephony != "twilio":
        return "BC_TELEPHONY no es 'twilio'"
    if not settings.public_url.startswith("https://"):
        return "BC_PUBLIC_URL debe ser una URL https pública (túnel o dominio)"
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return "Faltan BC_TWILIO_ACCOUNT_SID / BC_TWILIO_AUTH_TOKEN"
    if not stt_available:
        return "Falta BC_ASSEMBLYAI_API_KEY (STT en streaming)"
    if not tts_available:
        return "No hay TTS de servidor: instala Piper y define BC_PIPER_MODEL, o usa BC_TTS=workers_ai"
    return None


async def check_public_url(public_url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.get(public_url.rstrip("/") + HEALTH_PATH, timeout=10)
    r.raise_for_status()
    return dict(r.json())


async def configure_number(settings: Any, client: httpx.AsyncClient) -> dict[str, Any]:
    """Busca el número en la cuenta y le fija los webhooks. Devuelve un resumen."""
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    base = f"{API_TWILIO}/Accounts/{settings.twilio_account_sid}"
    r = await client.get(
        f"{base}/IncomingPhoneNumbers.json",
        params={"PhoneNumber": settings.twilio_number} if settings.twilio_number else {},
        auth=auth,
    )
    r.raise_for_status()
    numbers = r.json().get("incoming_phone_numbers", [])
    if not numbers:
        raise RuntimeError(
            f"La cuenta no tiene el número {settings.twilio_number or '(ninguno)'}: cómpralo en la consola de Twilio"
        )
    if len(numbers) > 1 and not settings.twilio_number:
        raise RuntimeError("La cuenta tiene varios números: indica cuál en BC_TWILIO_NUMBER")
    number = numbers[0]
    public_url = settings.public_url.rstrip("/")
    data = {
        "VoiceUrl": public_url + VOICE_PATH,
        "VoiceMethod": "POST",
        "StatusCallback": public_url + CALL_STATUS_PATH,
        "StatusCallbackMethod": "POST",
        "VoiceFallbackUrl": "",
    }
    r = await client.post(
        f"{base}/IncomingPhoneNumbers/{number['sid']}.json", data=data, auth=auth
    )
    r.raise_for_status()
    updated = r.json()
    return {
        "number": updated.get("phone_number"),
        "voice_url": updated.get("voice_url"),
        "status_callback": updated.get("status_callback"),
    }


TWIML_APP_NAME = "FinCall console"


async def ensure_twiml_app(settings: Any, client: httpx.AsyncClient) -> str:
    """TwiML App the console's browser calls go to (the same voice webhook as the number).
    Creates it, or updates the one with the same name. Returns its SID. A TwiML App needs no
    phone number and costs nothing until a call is made."""
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    base = f"{API_TWILIO}/Accounts/{settings.twilio_account_sid}"
    public_url = settings.public_url.rstrip("/")
    data = {
        "FriendlyName": TWIML_APP_NAME,
        "VoiceUrl": public_url + VOICE_PATH,
        "VoiceMethod": "POST",
        "StatusCallback": public_url + CALL_STATUS_PATH,
        "StatusCallbackMethod": "POST",
    }
    r = await client.get(
        f"{base}/Applications.json", params={"FriendlyName": TWIML_APP_NAME}, auth=auth
    )
    r.raise_for_status()
    apps = r.json().get("applications", [])
    url = f"{base}/Applications/{apps[0]['sid']}.json" if apps else f"{base}/Applications.json"
    r = await client.post(url, data=data, auth=auth)
    r.raise_for_status()
    return str(r.json()["sid"])


async def _main(argv: list[str]) -> int:
    from building_call.settings import settings
    from building_call.telephony.tts import create_tts

    parser = argparse.ArgumentParser(description="Prepara el número de Twilio para el voicebot")
    parser.add_argument("--verify", action="store_true", help="solo comprobar, sin tocar Twilio")
    parser.add_argument(
        "--twiml-app",
        action="store_true",
        help="crear o actualizar solo la TwiML App de las llamadas desde la consola (sin número)",
    )
    args = parser.parse_args(argv)

    tts = create_tts(settings)
    reason = not_ready_reason(settings, tts.available(), bool(settings.assemblyai_api_key))
    print(f"TTS: {tts.name} ({'ok' if tts.available() else 'no disponible'})")
    print(f"STT: {'AssemblyAI EU' if settings.assemblyai_api_key else 'sin clave'}")
    if reason:
        print(f"NO LISTO: {reason}")
        return 1
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            health = await check_public_url(settings.public_url, client)
        except Exception as e:  # noqa: BLE001
            print(f"NO LISTO: la URL pública no responde ({settings.public_url}{HEALTH_PATH}): {e}")
            print(
                "  ¿Está la API arrancada y el túnel abierto? (p. ej. cloudflared tunnel --url http://localhost:8000)"
            )
            return 1
        if not health.get("ok"):
            print(f"NO LISTO: la API pública dice: {health.get('reason')}")
            return 1
        print(f"URL pública OK: {settings.public_url}")
        if args.verify:
            print("Comprobación completa: todo listo para configurar el número.")
            return 0
        if args.twiml_app:
            app_sid = await ensure_twiml_app(settings, client)
            print(f"TwiML App «{TWIML_APP_NAME}» lista: BC_TWILIO_TWIML_APP_SID={app_sid}")
            return 0
        summary = await configure_number(settings, client)
    print(f"Número {summary['number']} configurado:")
    print(f"  voz:    {summary['voice_url']}")
    print(f"  estado: {summary['status_callback']}")
    print("Llama al número: debería contestar el asistente.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
