"""Banco de voz de punta a punta por LiveKit, con los mismos escenarios que `e2e_voice.py`.

Un participante sintético entra en la sala, habla con la voz del sistema (WAV de Windows SAPI)
y escucha al agente (`building_call.voice.agent`). Mide `response_ms` como la consola: del fin de
la voz del vecino al primer audio del bot. El escenario `barge_in` corta el resumen y
comprueba el ledger de entrega.

Uso (API con BC_AGENT_SECRET y LiveKit, agente arrancado, LiveKit en :7880):
    uv run --extra voice python evals/e2e_livekit.py
    uv run --extra voice python evals/e2e_livekit.py --solo interrupcion --verboso
Contra una API y un agente de pruebas, sin tocar los de la consola (agente arrancado con
BC_LIVEKIT_AGENT=fincall-pruebas y BC_AGENT_API_URL apuntando a esa API):
    uv run --extra voice python evals/e2e_livekit.py --api http://127.0.0.1:8100
        --agente fincall-pruebas --etiqueta u3-base
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from e2e_voice import SCENARIOS, _pcm_without_trailing_silence, synthesize
from livekit import api, rtc

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("BC_TEST_BANK_API", "http://localhost:8000")
AGENT = os.environ.get("BC_LIVEKIT_AGENT", "fincall")
LIVEKIT_URL = os.environ.get("BC_LIVEKIT_URL", "ws://localhost:7880")
KEY = os.environ.get("BC_LIVEKIT_API_KEY", "devkey")
LK_SECRET = os.environ.get("BC_LIVEKIT_API_SECRET", "secret")
AGENT_SECRET = os.environ.get("BC_AGENT_SECRET", "")

RATE = 48000
FRAME_MS = 10
BOT_VOICE_THRESHOLD = 500  # RMS del audio del agente que cuenta como voz
BOT_END_SILENCE_S = 0.9  # el bot ha terminado de hablar tras este silencio

INTERRUPTION: dict[str, Any] = {
    "name": "interrupcion",
    # Con la pregunta de detalle, el resumen llega tras el quinto turno
    "turns": [
        "Calle Mayor doce",
        "sí",
        "primero A",
        "Gotea el grifo del patio",
        "una gota cada poco",
    ],
    # Tras el último turno el bot lee el resumen: se le corta y se dice "sí" a lo que no oyó
    "cut_with": "sí, sí",
    "cut_after_s": 1.2,
}


class BotEar:
    """Escucha al agente y anota cuándo empieza y termina de sonar."""

    def __init__(self) -> None:
        self.voice_start: float | None = None
        self.last_voice = 0.0
        self.speaking = False

    async def listen(self, track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
            data = bytes(ev.frame.data)
            if audioop.rms(data, 2) > BOT_VOICE_THRESHOLD:
                now = time.monotonic()
                if not self.speaking:
                    self.speaking = True
                    self.voice_start = now
                self.last_voice = now
            elif self.speaking and time.monotonic() - self.last_voice > BOT_END_SILENCE_S:
                self.speaking = False

    async def wait_for_end(self, max_s: float = 30) -> None:
        t0 = time.monotonic()
        while (self.speaking or self.voice_start is None) and time.monotonic() - t0 < max_s:
            await asyncio.sleep(0.05)


class Mouth:
    """Micrófono sintético: trozos de 10 ms en tiempo real, silencio cuando no habla."""

    def __init__(self) -> None:
        self.source = rtc.AudioSource(RATE, 1)
        self._queue: list[bytes] = []
        self._samples = RATE * FRAME_MS // 1000

    def say(self, pcm: bytes) -> None:
        step = self._samples * 2
        self._queue += [pcm[i : i + step].ljust(step, b"\0") for i in range(0, len(pcm), step)]

    def silent(self) -> bool:
        return not self._queue

    async def pump(self) -> None:
        silence = b"\0" * self._samples * 2
        upcoming = time.monotonic()
        while True:
            data = self._queue.pop(0) if self._queue else silence
            await self.source.capture_frame(rtc.AudioFrame(data, RATE, 1, self._samples))
            upcoming += FRAME_MS / 1000
            await asyncio.sleep(max(0, upcoming - time.monotonic()))


def _to_48k(path: Path) -> bytes:
    pcm, rate = _pcm_without_trailing_silence(path)
    return audioop.ratecv(pcm, 2, 1, rate, RATE, None)[0]


async def run(
    scenario: dict[str, Any], http: httpx.AsyncClient, verbose: bool, agent: str = AGENT
) -> dict[str, Any]:
    view = (await http.post("/api/calls", json={"caller_phone": "+34600000000"})).json()
    cid = view["call_id"]
    if verbose:
        print(f"    llamada {cid}")
    room = f"banco-{cid}"
    token = (
        api.AccessToken(KEY, LK_SECRET)
        .with_identity(f"vecino-{cid}")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=agent, metadata=json.dumps({"call_id": cid})
                    )
                ]
            )
        )
        .to_jwt()
    )
    room = rtc.Room()
    heard_text, mouth = BotEar(), Mouth()
    tasks: list[asyncio.Task[Any]] = []

    @room.on("track_subscribed")
    def _track(track: rtc.Track, *_: Any) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.ensure_future(heard_text.listen(track)))

    await room.connect(LIVEKIT_URL, token)
    track = rtc.LocalAudioTrack.create_audio_track("microphone", mouth.source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    tasks.append(asyncio.ensure_future(mouth.pump()))
    latencies: list[int] = []
    try:
        await heard_text.wait_for_end(40)  # el saludo
        for text in scenario["turns"]:
            await heard_text.wait_for_end()
            mouth.say(_to_48k(synthesize(text)))
            while not mouth.silent():
                await asyncio.sleep(0.01)
            t_end = time.monotonic()
            heard_text.voice_start = None
            while heard_text.voice_start is None and time.monotonic() - t_end < 15:
                await asyncio.sleep(0.01)
            if heard_text.voice_start is None:
                return {
                    "name": scenario["name"],
                    "ok": False,
                    "reason": f"sin voz tras {text!r}",
                }
            latencies.append(int((heard_text.voice_start - t_end) * 1000))
        if cut := scenario.get("cut_with"):
            await asyncio.sleep(scenario["cut_after_s"])  # el resumen está sonando
            mouth.say(_to_48k(synthesize(cut)))
            await asyncio.sleep(6)
            v = (await http.get(f"/api/calls/{cid}")).json()
            return _check_interruption(scenario, v, latencies, verbose)
        await heard_text.wait_for_end()
        await asyncio.sleep(2)  # que el agente informe de la última entrega
    finally:
        for t in tasks:
            t.cancel()
        await room.disconnect()
    final = (await http.get(f"/api/calls/{cid}")).json()
    if verbose:
        _transcript(scenario, final, latencies)
    data = final["data"]
    failures = []
    for k, v in scenario["wait"].items():
        if k.endswith("~"):
            if not str(data.get(k[:-1]) or "").lower().startswith(str(v).lower()):
                failures.append(f"{k}: esperado ~{v!r}, obtenido {data.get(k[:-1])!r}")
        elif data.get(k) != v:
            failures.append(f"{k}: esperado {v!r}, obtenido {data.get(k)!r}")
    return {
        "name": scenario["name"],
        "ok": not failures,
        "reason": "; ".join(failures),
        "latencies_ms": latencies,
    }


def _transcript(scenario: dict[str, Any], v: dict[str, Any], latencies: list[int]) -> None:
    print(f"    guion: {scenario['turns']}")
    print(f"    latencias: {latencies}")
    for h in v["history"]:
        print(f"    {h['role'].upper():6} {h['text']}")


def _check_interruption(
    scenario: dict[str, Any], v: dict[str, Any], latencies: list[int], verbose: bool
) -> dict[str, Any]:
    trace = [t["action"] for t in v["trace"]]
    if verbose:
        _transcript(scenario, v, latencies)
        print(f"    entrega: {json.dumps(v.get('last_delivery'), ensure_ascii=False)[:400]}")
    failures = []
    if v["data"]["result"] == "incident_created":
        failures.append("el sí sobre un resumen no oído confirmó la incidencia")
    if "confirmation_unheard" not in trace and "repeat_not_heard" not in trace:
        failures.append(f"no se repitió el resumen (traza: {trace[-4:]})")
    return {
        "name": scenario["name"],
        "ok": not failures,
        "reason": "; ".join(failures),
        "latencies_ms": latencies,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--api", default=BASE, help="URL de la API (BC_TEST_BANK_API)")
    ap.add_argument("--agent", default=AGENT, help="agente al que despachar (BC_LIVEKIT_AGENT)")
    ap.add_argument("--label", help="guarda también last_e2e_livekit-<etiqueta>.json")
    args = ap.parse_args()
    if not AGENT_SECRET:
        sys.exit("Falta BC_AGENT_SECRET (el banco entra en la API como el agente)")
    scenarios = [
        e for e in [*SCENARIOS, INTERRUPTION] if not args.only or e["name"] == args.only
    ]
    results = []
    async with httpx.AsyncClient(
        base_url=args.api, headers={"Authorization": f"Bearer {AGENT_SECRET}"}, timeout=30
    ) as http:
        for e in scenarios:
            print(f"== {e['name']}")
            r = await run(e, http, args.verbose, args.agent)
            results.append(r)
            lat = sorted(r.get("latencies_ms", []))
            print(
                f"   {'OK ' if r['ok'] else 'FAILURE'} {r.get('reason', '')} | respuesta p50 "
                f"{lat[len(lat) // 2] if lat else '-'} ms max {lat[-1] if lat else '-'} ms"
            )
    todas = sorted(x for r in results for x in r.get("latencies_ms", []))
    if todas:
        p95 = todas[min(len(todas) - 1, round(0.95 * (len(todas) - 1)))]
        print(
            f"\nrespuesta_ms global: p50 {todas[len(todas) // 2]} ms, p95 {p95} ms (n={len(todas)})"
        )
    ok = sum(1 for r in results if r["ok"])
    print(f"{ok}/{len(results)} escenarios correctos")
    output = json.dumps(results, ensure_ascii=False, indent=2)
    (HERE / "last_e2e_livekit.json").write_text(output, encoding="utf-8")
    if args.label:
        (HERE / f"last_e2e_livekit-{args.label}.json").write_text(output, encoding="utf-8")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
