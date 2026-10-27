"""Banco de voz por SIP: el vecino sintético llama de verdad por SIP al número de entrada.

LiveKit marca por un trunk de salida que apunta a su propio `livekit-sip` (en local, el
contenedor de compose): la llamada sale y vuelve a entrar por SIP/RTP con G.711, pasa por el
trunk de entrada y la regla de despacho (`building_call.telephony.sip`) y la atiende el agente
como a un teléfono. Mismos escenarios y medida de `response_ms` que `e2e_livekit.py`, con el
audio telefónico de por medio.

Uso (API, agente y LiveKit SIP arrancados; `configure_sip` ya ejecutado):
    uv run --extra voice python evals/e2e_sip.py --api http://127.0.0.1:8100 --verbose
    uv run --extra voice python evals/e2e_sip.py --only fuga_simple
Lee del entorno o de `backend/.env`: BC_SIP_NUMBER, BC_SIP_AUTH_USERNAME/PASSWORD,
BC_AGENT_SECRET y las claves de LiveKit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from typing import Any

import httpx
from e2e_livekit import HERE, INTERRUPTION, BotEar, Mouth, _check_interruption, _to_48k
from e2e_voice import SCENARIOS, synthesize
from livekit import api, rtc

from building_call.settings import settings
from building_call.telephony.sip import ROOM_PREFIX

OUT_TRUNK = "fincall-banco-salida"
#: Dirección del `livekit-sip` vista desde él mismo (comparte red con `livekit` en compose)
SIP_ADDRESS = "127.0.0.1:5060"

HANDOFF: dict[str, Any] = {
    "name": "pide_persona",
    "turns": ["Quiero hablar con una persona, por favor"],
    "wait": {"result": "handed_to_administrator"},
}


async def outbound_trunk(lk: api.LiveKitAPI) -> str:
    """Trunk de salida del banco hacia nuestra propia entrada (idempotente)."""
    trunk = api.SIPOutboundTrunkInfo(
        name=OUT_TRUNK,
        address=SIP_ADDRESS,
        numbers=["+34600000000"],
        auth_username=settings.sip_auth_username,
        auth_password=settings.sip_auth_password,
    )
    existing = (await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())).items
    if found := next((t for t in existing if t.name == OUT_TRUNK), None):
        return (await lk.sip.update_outbound_trunk(found.sip_trunk_id, trunk)).sip_trunk_id
    req = api.CreateSIPOutboundTrunkRequest(trunk=trunk)
    return (await lk.sip.create_outbound_trunk(req)).sip_trunk_id


async def find_call(lk: api.LiveKitAPI, caller: str, max_s: float = 15) -> str | None:
    """El expediente que abrió el agente para esta llamada: sala tel-… con nuestro número."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        rooms = (await lk.room.list_rooms(api.ListRoomsRequest())).rooms
        for r in rooms:
            if not r.name.startswith(ROOM_PREFIX):
                continue
            people = await lk.room.list_participants(api.ListParticipantsRequest(room=r.name))
            phones = [p.attributes.get("sip.phoneNumber") for p in people.participants]
            ids = [p.attributes.get("fincall.call_id") for p in people.participants]
            if caller in phones and any(ids):
                return next(i for i in ids if i)
        await asyncio.sleep(0.3)
    return None


async def run(
    scenario: dict[str, Any],
    lk: api.LiveKitAPI,
    http: httpx.AsyncClient,
    trunk_id: str,
    verbose: bool,
) -> dict[str, Any]:
    # Un número de vecino por llamada: así se encuentra su sala y su expediente
    caller = f"+3460{secrets.randbelow(10**7):07d}"
    room_name = f"banco-sip-{secrets.token_hex(4)}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity("vecino")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    room = rtc.Room()
    ear, mouth = BotEar(), Mouth()
    tasks: list[asyncio.Task[Any]] = []

    @room.on("track_subscribed")
    def _track(track: rtc.Track, *_: Any) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.ensure_future(ear.listen(track)))

    await room.connect(settings.livekit_url, token)
    track = rtc.LocalAudioTrack.create_audio_track("microphone", mouth.source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    tasks.append(asyncio.ensure_future(mouth.pump()))
    latencies: list[int] = []
    t_dial = time.monotonic()
    try:
        await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=settings.sip_number,
                sip_number=caller,
                room_name=room_name,
                participant_identity="linea",
                wait_until_answered=True,
            )
        )
        answered_ms = int((time.monotonic() - t_dial) * 1000)
        cid = await find_call(lk, caller)
        if verbose:
            print(f"    contestada en {answered_ms} ms · llamada {cid}")
        if cid is None:
            return {"name": scenario["name"], "ok": False, "reason": "sin expediente del agente"}
        await ear.wait_for_end(40)  # el saludo
        greeting_ms = int(((ear.voice_start or t_dial) - t_dial) * 1000)
        for text in scenario["turns"]:
            await ear.wait_for_end()
            # Si el bot ya cerró (lo entendió todo antes de lo que preveía el guion), se evalúa
            if (await http.get(f"/api/calls/{cid}")).json()["finished"]:
                break
            mouth.say(_to_48k(synthesize(text)))
            while not mouth.silent():
                await asyncio.sleep(0.01)
            t_end = time.monotonic()
            ear.voice_start = None
            while ear.voice_start is None and time.monotonic() - t_end < 15:
                await asyncio.sleep(0.01)
            if ear.voice_start is None:
                return {"name": scenario["name"], "ok": False, "reason": f"sin voz tras {text!r}"}
            if verbose:  # reloj de pared: se cruza con los logs del agente
                print(
                    f"    t_fin_voz={time.time() - (time.monotonic() - t_end):.3f} "
                    f"t_bot={time.time() - (time.monotonic() - ear.voice_start):.3f}"
                )
            latencies.append(int((ear.voice_start - t_end) * 1000))
        if cut := scenario.get("cut_with"):
            await asyncio.sleep(scenario["cut_after_s"])
            mouth.say(_to_48k(synthesize(cut)))
            await asyncio.sleep(6)
            v = (await http.get(f"/api/calls/{cid}")).json()
            return _check_interruption(scenario, v, latencies, verbose)
        await ear.wait_for_end()
        await asyncio.sleep(2)
    finally:
        for t in tasks:
            t.cancel()
        await room.disconnect()
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    final = (await http.get(f"/api/calls/{cid}")).json()
    if verbose:
        print(f"    saludo a {greeting_ms} ms de marcar · latencias: {latencies}")
        for h in final["history"]:
            print(f"    {h['role'].upper():6} {h['text']}")
    failures = [
        f"{k}: esperado {v!r}, obtenido {final['data'].get(k.rstrip('~'))!r}"
        for k, v in scenario["wait"].items()
        if not (
            str(final["data"].get(k[:-1]) or "").lower().startswith(str(v).lower())
            if k.endswith("~")
            else final["data"].get(k) == v
        )
    ]
    return {
        "name": scenario["name"],
        "ok": not failures,
        "reason": "; ".join(failures),
        "latencies_ms": latencies,
        "answered_ms": answered_ms,
        "greeting_ms": greeting_ms,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    if not (settings.agent_secret and settings.sip_number and settings.sip_auth_password):
        sys.exit("Faltan BC_AGENT_SECRET, BC_SIP_NUMBER o BC_SIP_AUTH_PASSWORD")
    scenarios = [
        e for e in [*SCENARIOS, INTERRUPTION, HANDOFF] if not args.only or e["name"] == args.only
    ]
    results = []
    async with (
        api.LiveKitAPI(
            settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
        ) as lk,
        httpx.AsyncClient(
            base_url=args.api,
            headers={"Authorization": f"Bearer {settings.agent_secret}"},
            timeout=30,
        ) as http,
    ):
        trunk_id = await outbound_trunk(lk)
        for e in scenarios:
            print(f"== {e['name']}")
            try:
                r = await run(e, lk, http, trunk_id, args.verbose)
            except Exception as exc:  # noqa: BLE001 - un escenario roto no para el banco
                r = {"name": e["name"], "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            results.append(r)
            lat = sorted(r.get("latencies_ms", []))
            print(
                f"   {'OK ' if r['ok'] else 'FAILURE'} {r.get('reason', '')} | respuesta p50 "
                f"{lat[len(lat) // 2] if lat else '-'} ms max {lat[-1] if lat else '-'} ms"
            )
    every = sorted(x for r in results for x in r.get("latencies_ms", []))
    if every:
        p95 = every[min(len(every) - 1, round(0.95 * (len(every) - 1)))]
        print(
            f"\nresponse_ms por SIP: p50 {every[len(every) // 2]} ms, p95 {p95} ms (n={len(every)})"
        )
    ok = sum(1 for r in results if r["ok"])
    print(f"{ok}/{len(results)} escenarios correctos")
    output = json.dumps(results, ensure_ascii=False, indent=2)
    (HERE / "last_e2e_sip.json").write_text(output, encoding="utf-8")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
