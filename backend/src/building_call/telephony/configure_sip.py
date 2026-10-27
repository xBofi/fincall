"""Deja LiveKit listo para recibir llamadas SIP. Uso:

    cd backend
    uv run python -m building_call.telephony.configure_sip            # comprueba y configura
    uv run python -m building_call.telephony.configure_sip --verify   # solo comprueba

Lee `backend/.env`: BC_LIVEKIT_URL/API_KEY/API_SECRET, BC_SIP_NUMBER, BC_SIP_AUTH_USERNAME/PASSWORD
o BC_SIP_ALLOWED_ADDRESSES, BC_HANDOFF_TARGET (opcional). Crea o actualiza el trunk de entrada y
la regla que despacha cada llamada a `BC_LIVEKIT_AGENT`. Idempotente. No imprime secretos.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _main(argv: list[str]) -> int:
    from livekit import api

    from building_call.settings import settings
    from building_call.telephony import sip

    parser = argparse.ArgumentParser(description="Prepara la entrada SIP de LiveKit")
    parser.add_argument("--verify", action="store_true", help="solo comprobar, sin tocar LiveKit")
    args = parser.parse_args(argv)

    if reason := sip.not_ready_reason(settings):
        print(f"NO LISTO: {reason}")
        return 1
    print(f"Número: {settings.sip_number} · agente: {settings.livekit_agent}")
    print(f"Transferencia a humano: {settings.handoff_target or 'no (se cuelga)'}")
    if args.verify:
        print("Comprobación completa: todo listo para configurar LiveKit.")
        return 0
    async with api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    ) as lk:
        summary = await sip.configure(lk.sip, settings)
    print(f"Trunk {summary['trunk_id']} (autenticación por {summary['authentication']})")
    print(f"Regla {summary['rule_id']}: cada llamada a su sala con el agente {summary['agent']}")
    print(f"Llama a sip:{summary['number']}@<host de livekit-sip>: debería contestar el asistente.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
