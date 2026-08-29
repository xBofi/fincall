"""Deja el número de Twilio apuntando a esta API. Uso:

    cd backend
    uv run python -m building_call.telefonia.configurar            # comprueba y configura
    uv run python -m building_call.telefonia.configurar --comprobar  # solo comprueba

Lee `backend/.env`: BC_TELEFONIA=twilio, BC_URL_PUBLICA, BC_TWILIO_ACCOUNT_SID, BC_TWILIO_AUTH_TOKEN,
BC_TWILIO_NUMERO (E.164). Comprueba que la URL pública responde, que hay STT y TTS, y fija en el
número el webhook de voz (POST) y el de estado de llamada. Idempotente. No imprime secretos.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import httpx

from building_call.telefonia.twilio import RUTA_ESTADO_LLAMADA, RUTA_SALUD, RUTA_VOZ

API_TWILIO = "https://api.twilio.com/2010-04-01"


def motivo_no_listo(settings: Any, tts_disponible: bool, stt_disponible: bool) -> str | None:
    """Explica (sin secretos) por qué la telefonía aún no puede atender llamadas."""
    if settings.telefonia != "twilio":
        return "BC_TELEFONIA no es 'twilio'"
    if not settings.url_publica.startswith("https://"):
        return "BC_URL_PUBLICA debe ser una URL https pública (túnel o dominio)"
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return "Faltan BC_TWILIO_ACCOUNT_SID / BC_TWILIO_AUTH_TOKEN"
    if not stt_disponible:
        return "Falta BC_ASSEMBLYAI_API_KEY (STT en streaming)"
    if not tts_disponible:
        return "No hay TTS de servidor: instala Piper y define BC_PIPER_MODELO, o usa BC_TTS=workers_ai"
    return None


async def comprobar_url_publica(url_publica: str, cliente: httpx.AsyncClient) -> dict[str, Any]:
    r = await cliente.get(url_publica.rstrip("/") + RUTA_SALUD, timeout=10)
    r.raise_for_status()
    return dict(r.json())


async def configurar_numero(settings: Any, cliente: httpx.AsyncClient) -> dict[str, Any]:
    """Busca el número en la cuenta y le fija los webhooks. Devuelve un resumen."""
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    base = f"{API_TWILIO}/Accounts/{settings.twilio_account_sid}"
    r = await cliente.get(
        f"{base}/IncomingPhoneNumbers.json",
        params={"PhoneNumber": settings.twilio_numero} if settings.twilio_numero else {},
        auth=auth,
    )
    r.raise_for_status()
    numeros = r.json().get("incoming_phone_numbers", [])
    if not numeros:
        raise RuntimeError(
            f"La cuenta no tiene el número {settings.twilio_numero or '(ninguno)'}: cómpralo en la consola de Twilio"
        )
    if len(numeros) > 1 and not settings.twilio_numero:
        raise RuntimeError("La cuenta tiene varios números: indica cuál en BC_TWILIO_NUMERO")
    numero = numeros[0]
    publica = settings.url_publica.rstrip("/")
    datos = {
        "VoiceUrl": publica + RUTA_VOZ,
        "VoiceMethod": "POST",
        "StatusCallback": publica + RUTA_ESTADO_LLAMADA,
        "StatusCallbackMethod": "POST",
        "VoiceFallbackUrl": "",
    }
    r = await cliente.post(
        f"{base}/IncomingPhoneNumbers/{numero['sid']}.json", data=datos, auth=auth
    )
    r.raise_for_status()
    actualizado = r.json()
    return {
        "numero": actualizado.get("phone_number"),
        "voice_url": actualizado.get("voice_url"),
        "status_callback": actualizado.get("status_callback"),
    }


async def _main(argv: list[str]) -> int:
    from building_call.settings import settings
    from building_call.telefonia.tts import crear_tts

    parser = argparse.ArgumentParser(description="Prepara el número de Twilio para el voicebot")
    parser.add_argument("--comprobar", action="store_true", help="solo comprobar, sin tocar Twilio")
    args = parser.parse_args(argv)

    tts = crear_tts(settings)
    motivo = motivo_no_listo(settings, tts.disponible(), bool(settings.assemblyai_api_key))
    print(f"TTS: {tts.nombre} ({'ok' if tts.disponible() else 'no disponible'})")
    print(f"STT: {'AssemblyAI EU' if settings.assemblyai_api_key else 'sin clave'}")
    if motivo:
        print(f"NO LISTO: {motivo}")
        return 1
    async with httpx.AsyncClient(timeout=20) as cliente:
        try:
            salud = await comprobar_url_publica(settings.url_publica, cliente)
        except Exception as e:  # noqa: BLE001
            print(f"NO LISTO: la URL pública no responde ({settings.url_publica}{RUTA_SALUD}): {e}")
            print(
                "  ¿Está la API arrancada y el túnel abierto? (p. ej. cloudflared tunnel --url http://localhost:8000)"
            )
            return 1
        if not salud.get("ok"):
            print(f"NO LISTO: la API pública dice: {salud.get('motivo')}")
            return 1
        print(f"URL pública OK: {settings.url_publica}")
        if args.comprobar:
            print("Comprobación completa: todo listo para configurar el número.")
            return 0
        resumen = await configurar_numero(settings, cliente)
    print(f"Número {resumen['numero']} configurado:")
    print(f"  voz:    {resumen['voice_url']}")
    print(f"  estado: {resumen['status_callback']}")
    print("Llama al número: debería contestar el asistente.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
