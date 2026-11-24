"""Banco de pruebas de voz de punta a punta con audio sintético.

Genera WAV en español con la voz del sistema (Windows SAPI, "Microsoft Helena"), los envía al
WebSocket de voz del backend (AssemblyAI EU -> grafo -> LLM -> redacción) y comprueba el
resultado de la llamada. Sirve para probar cómo habla "una persona normal", no alguien que
sabe qué decir.

Uso (backend con BC_ASSEMBLYAI_API_KEY y BC_AGENT_SECRET; el banco entra con ese secreto, como
el agente de voz, porque la consola solo admite Google o Microsoft). BC_TEST_BANK_API cambia la API
(por defecto http://localhost:8000):
    uv run python evals/e2e_voice.py            # todos los escenarios
    uv run python evals/e2e_voice.py --solo natural_todo_junto --verboso
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import httpx
import websockets

HERE = Path(__file__).resolve().parent
AUDIO = HERE / "synthetic_audio"
BASE = os.environ.get("BC_TEST_BANK_API", "http://localhost:8000")

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "guiado",
        "turns": [
            "Calle Mayor doce",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño",
            "sale con fuerza, del techo",
            "sí",
        ],
        "wait": {
            "result": "incident_created",
            "home_id": "finca-mayor-12-u-3B",
            "category": "plumbing",
        },
    },
    {
        "name": "natural_all_at_once",
        "turns": [
            "Hola, buenas, mire, llamo desde la Avenida de la Alameda cinco, portal dos, tercero A. Soy Carmen y tengo una fuga de agua en el baño que está inundando el suelo",
            "sí, eso es",
            "sale con fuerza, creo que del techo",
            "sí, correcto",
        ],
        "wait": {
            "result": "incident_created",
            "home_id": "finca-alameda-5-2-3A",
            "category": "plumbing",
        },
    },
    {
        "name": "fillers_and_greeting",
        "turns": [
            "Hola, buenas.",
            "Sí, vivo en la calle Mayor número doce.",
            "Sí.",
            "Ah, vale, perdón. Pues sí, piso uno, puerta A.",
            "Con Laura Gómez Ruiz.",
            "Mira, hay que pintar las paredes del corredor porque no están nada bien.",
            "El corredor del primero, tienen humedad y desconchones.",
            "Sí, es correcto.",
        ],
        "wait": {"result": "incident_created", "home_id": "finca-mayor-12-u-1A"},
    },
    {
        "name": "ambiguous_entrance",
        "turns": [
            "Avenida de la Alameda cinco",
            "sí",
            "El tercero A es mi vivienda",
            "portal uno",
            "El ascensor hace un ruido raro",
            "funciona, pero hace ruido al arrancar",
            "sí",
        ],
        "wait": {
            "result": "incident_created",
            "home_id": "finca-alameda-5-1-3A",
            "category": "elevator",
        },
    },
    {
        "name": "name_correction",
        "turns": [
            "Calle Mayor doce",
            "sí",
            "segundo B, soy Pepe",
            "No funciona la luz de la escalera",
            "son todas, no encienden",
            "No, me llamo Pedro García",
            "sí",
        ],
        "wait": {
            "result": "incident_created",
            "nombre_declarado~": "Pedro",
            "category": "electrical",
        },
    },
    {
        "name": "question_and_human",
        "turns": [
            "¿Esta llamada se está grabando?",
            "Calle Mayor doce",
            "sí",
            "Prefiero hablar con una persona, por favor",
        ],
        "wait": {"result": "handed_to_administrator"},
    },
    {
        "name": "nonexistent_home_and_help",
        "turns": [
            "Calle Mayor doce",
            "sí",
            "piso uno, puerta dos",
            "piso uno, puerta B",
            "Gotea el grifo del patio",
            "una gota cada poco, desde ayer",
            "sí",
        ],
        "wait": {"result": "incident_created", "home_id": "finca-mayor-12-u-1B"},
    },
]


def synthesize(text: str) -> Path:
    AUDIO.mkdir(exist_ok=True)
    # "-utf8" en el nombre: los WAV anteriores se generaron sin UTF-8 en la entrada de PowerShell
    # y la voz decía "S" por "sí" y "BAO" por "baño"
    path = AUDIO / (hashlib.md5(text.encode("utf-8")).hexdigest()[:12] + "-utf8.wav")
    if path.exists():
        return path
    guion = (
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'es*' } | Select-Object -First 1; "
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak([Console]::In.ReadToEnd()); $s.SetOutputToNull()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", guion],
        input=text.encode("utf-8"),
        check=True,
        capture_output=True,
    )
    return path


def _pcm_without_trailing_silence(path: Path) -> tuple[bytes, int]:
    w = wave.open(str(path), "rb")
    rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    step = int(rate * 0.02) * 2
    i = len(pcm)
    while i > step and audioop.rms(pcm[i - step : i], 2) <= 300:
        i -= step
    return pcm[:i], rate


def api_session() -> httpx.Client:
    """Cliente de la API con el secreto del agente (las rutas de llamada y el WebSocket de voz lo
    aceptan). Sin secreto, sin cabecera: la API responderá 401."""
    secret = os.environ.get("BC_AGENT_SECRET", "")
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    return httpx.Client(base_url=BASE, timeout=30, headers=headers)


def _first_audio_ms(http: httpx.Client, text: str) -> int | None:
    """Lo que tarda la voz del servidor en dar su primer audio (como la consola con ElevenLabs)."""
    t0 = time.time()
    primero: int | None = None
    try:
        with http.stream("POST", "/api/voz-bot", json={"text": text[:800]}) as r:
            if r.status_code != 200:
                return None
            # Se consume entero: cortar el audio a medias deja la voz del servidor ocupada y la
            # petición siguiente espera ~2 s (medido 2026-10-21)
            for _ in r.iter_bytes():
                if primero is None:
                    primero = int((time.time() - t0) * 1000)
    except httpx.HTTPError:
        return None
    return primero


HTTP = api_session()


async def run(scenario: dict[str, Any], verbose: bool) -> dict[str, Any]:
    call = HTTP.post("/api/calls", json={"caller_phone": "+34600000000"}).json()
    cid = call["call_id"]
    routes = [synthesize(t) for t in scenario["turns"]]
    _, rate = _pcm_without_trailing_silence(routes[0])
    latencies: list[int] = []
    transcripts: list[str] = []
    last: dict[str, Any] = call
    if verbose:
        print(f"    BOT: {' '.join(call['bot_messages'])}")
    async with websockets.connect(
        f"{BASE.replace('http', 'ws', 1)}/api/calls/{cid}/voice?sample_rate={rate}",
        open_timeout=30,
        additional_headers={"Authorization": HTTP.headers["authorization"]}
        if "authorization" in HTTP.headers
        else None,
    ) as ws:
        await ws.recv()  # listo
        chunk = int(rate * 0.1) * 2
        for text, path in zip(scenario["turns"], routes, strict=True):
            pcm, _ = _pcm_without_trailing_silence(path)
            for i in range(0, len(pcm), chunk):
                await ws.send(pcm[i : i + chunk])
                await asyncio.sleep(0.1)
            t_end = time.time()
            reply = None
            final = ""
            while time.time() - t_end < 12 and reply is None:
                await ws.send(b"\x00" * chunk)
                try:
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.1))
                        if m["type"] == "final":
                            final = m["text"]
                        elif m["type"] == "reply":
                            reply = m
                        elif m["type"] == "error":
                            print("    ERROR", m)
                except TimeoutError:
                    pass
            if reply is None:
                return {
                    "name": scenario["name"],
                    "ok": False,
                    "reason": f"sin respuesta a {text!r}",
                }
            latencies.append(int((time.time() - t_end) * 1000))
            # Hasta que suena: se suma el primer audio de la voz del servidor (la de producción)
            if os.environ.get("BC_TEST_BANK_WITH_VOICE") and reply["bot_messages"]:
                voice_ms = _first_audio_ms(HTTP, " ".join(reply["bot_messages"]))
                if voice_ms is not None:
                    latencies[-1] += voice_ms
            transcripts.append(final)
            last = reply
            if verbose:
                print(f"    VECINO dijo: {text!r}\n       STT oyó: {final!r}")
                print(
                    f"    BOT [{reply['current_node']}, {latencies[-1]} ms]: {' '.join(reply['bot_messages'])}"
                )
            if reply["finished"]:
                break
    data = last["data"]
    failures = []
    for k, v in scenario["wait"].items():
        if k.endswith("~"):  # coincidencia laxa (el STT pierde acentos en nombres)
            if not str(data.get(k[:-1]) or "").lower().startswith(str(v).lower()):
                failures.append(f"{k}: esperado ~{v!r}, obtenido {data.get(k[:-1])!r}")
        elif data.get(k) != v:
            failures.append(f"{k}: esperado {v!r}, obtenido {data.get(k)!r}")
    return {
        "name": scenario["name"],
        "ok": not failures,
        "reason": "; ".join(failures),
        "latencies_ms": latencies,
        "transcripts": transcripts,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    scenarios = [e for e in SCENARIOS if not args.only or e["name"] == args.only]
    results = []
    for e in scenarios:
        print(f"== {e['name']}")
        r = await run(e, args.verbose)
        results.append(r)
        lat = r.get("latencies_ms", [])
        print(
            f"   {'OK ' if r['ok'] else 'FAILURE'} {r.get('reason', '')} | fin_voz->respuesta p50 {sorted(lat)[len(lat) // 2] if lat else '-'} ms max {max(lat) if lat else '-'} ms"
        )
    ok = sum(1 for r in results if r["ok"])
    print(f"\n{ok}/{len(results)} escenarios correctos")
    (HERE / "last_e2e_voice.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
