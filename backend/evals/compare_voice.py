"""Compara proveedores de voz para elegir con datos: síntesis (TTS) y transcripción (STT).

TTS: para frases reales del asistente mide el tiempo hasta el primer audio (lo que se ganaría
reproduciendo en streaming) y el tiempo total (lo que paga hoy el pipeline, que sintetiza la
frase entera antes de enviarla). Guarda los audios con letras anónimas para una escucha a
ciegas: `listening.html` no dice qué proveedor es cada letra; `key.json` sí.

STT: envía las frases de `e2e_voice.py` (voz sintética de Windows) a ritmo real, como haría una
llamada, y mide la latencia de fin de turno (último audio con voz → transcripción final) y la
tasa de error por palabra (WER) contra el texto original.

Proveedores (se omiten los que no tienen credenciales en backend/.env):
- TTS: piper (local), melotts y aura-2-es (Cloudflare Workers AI), elevenlabs
  (BC_ELEVENLABS_API_KEY + BC_ELEVENLABS_VOICE, uno o varios voice ID separados por comas;
  BC_ELEVENLABS_URL para el endpoint de residencia UE).
- STT: assemblyai (endpoint UE), nova-3 y flux (Workers AI a través de AI Gateway,
  BC_CLOUDFLARE_GATEWAY, por defecto "default").

Uso:
    uv run python evals/compare_voice.py tts --repeticiones 3
    uv run python evals/compare_voice.py stt --proveedores assemblyai,nova-3
Resultados en evals/voice_results/<fecha>/ (ignorado por git: contiene audio).
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
import os
import random
import sys
import wave
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from building_call.graph.nodes import _normalize  # noqa: E402
from building_call.settings import Settings  # noqa: E402
from building_call.stt_streaming import AssemblyAIStreamingSession  # noqa: E402
from building_call.telephony.tts import PiperSynthesizer  # noqa: E402

RATE = 16000
CF_API = "https://api.cloudflare.com/client/v4/accounts/{counts}/ai/run/{model}"
CF_GATEWAY_WS = "wss://gateway.ai.cloudflare.com/v1/{counts}/{gateway}/workers-ai"

# Frases reales del asistente: corta, pregunta, resumen largo y cierre largo
PHRASES = [
    "He identificado Calle Mayor 12. ¿Es correcto?",
    "Gracias. ¿Cuál es su vivienda? Indíqueme portal si lo hay, piso y puerta.",
    "Le resumo: Marta, 3º B. Fuga de agua en el baño que está inundando el suelo. "
    "Lo he clasificado como fontanería, con urgencia alta. ¿Es correcto?",
    "He registrado su incidencia. Por su urgencia aviso ahora mismo a Fontanería Pérez, que se "
    "pondrá en contacto con usted lo antes posible. Gracias por su llamada, hasta luego.",
]


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    order = sorted(values)
    return order[max(0, min(len(order) - 1, round(q * (len(order) - 1))))]


def _summary(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "p50": _p(values, 0.5), "p95": _p(values, 0.95)}


def _save_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


async def _to_pcm(audio: bytes, ffmpeg: str = "ffmpeg") -> bytes:
    """Cualquier formato (mp3, wav...) → PCM16 mono 16 kHz."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(RATE),
        "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    pcm, err = await proc.communicate(audio)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err.decode(errors='ignore')[:200]}")
    return pcm


# --------------------------------------------------------------------------------------- TTS

# Cada proveedor devuelve (primer_audio_s, total_s, pcm16_16k)
Synthesis = Callable[[str], Awaitable[tuple[float, float, bytes]]]


async def _http_streaming(
    client: httpx.AsyncClient, url: str, **kw: Any
) -> tuple[float, float, bytes]:
    t0 = perf_counter()
    primero: float | None = None
    chunks: list[bytes] = []
    async with client.stream("POST", url, **kw) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {(await r.aread())[:300]!r}")
        async for chunk in r.aiter_bytes():
            if primero is None and chunk:
                primero = perf_counter() - t0
            chunks.append(chunk)
    total = perf_counter() - t0
    return primero or total, total, b"".join(chunks)


def tts_providers(
    s: Settings, env: dict[str, str | None], client: httpx.AsyncClient
) -> dict[str, Synthesis]:
    available_synths: dict[str, Synthesis] = {}

    piper = PiperSynthesizer(s.piper_bin, s.piper_model)
    if piper.available():

        async def with_piper(text: str) -> tuple[float, float, bytes]:
            # Sin la caché del sintetizador: se mide la síntesis, no una consulta a un dict
            t0 = perf_counter()
            if piper._package_available():
                pcm = await asyncio.to_thread(piper._synthesize_in_process, text)
            else:
                pcm = await piper._synthesize_subprocess(text)
            total = perf_counter() - t0
            if piper.rate != RATE:
                pcm, _ = audioop.ratecv(pcm, 2, 1, int(piper.rate), RATE, None)
            return total, total, pcm

        available_synths["piper"] = with_piper

    if s.cloudflare_account_id and s.cloudflare_api_token:
        headers = {"Authorization": f"Bearer {s.cloudflare_api_token}"}

        async def with_melotts(text: str) -> tuple[float, float, bytes]:
            url = CF_API.format(counts=s.cloudflare_account_id, model="@cf/myshell-ai/melotts")
            t0 = perf_counter()
            r = await client.post(url, headers=headers, json={"prompt": text, "lang": "es"})
            r.raise_for_status()
            mp3 = base64.b64decode(r.json()["result"]["audio"])
            total = perf_counter() - t0  # devuelve JSON con todo el audio: primero = total
            return total, total, await _to_pcm(mp3)

        available_synths["melotts"] = with_melotts

        for voice in ("javier", "carina"):

            async def with_aura(text: str, voice: str = voice) -> tuple[float, float, bytes]:
                url = CF_API.format(counts=s.cloudflare_account_id, model="@cf/deepgram/aura-2-es")
                primero, total, audio = await _http_streaming(
                    client, url, headers=headers, json={"text": text, "speaker": voice}
                )
                return primero, total, await _to_pcm(audio)

            available_synths[f"aura-2-es/{voice}"] = with_aura

    # Varias voces separadas por comas; la variable de entorno manda sobre .env para probar sin editarlo.
    eleven_voices = os.environ.get("BC_ELEVENLABS_VOICE") or env.get("BC_ELEVENLABS_VOICE") or ""
    if env.get("BC_ELEVENLABS_API_KEY") and eleven_voices:
        base = (env.get("BC_ELEVENLABS_URL") or "https://api.elevenlabs.io").rstrip("/")

        for voice_id in [v.strip() for v in eleven_voices.split(",") if v.strip()]:

            async def with_elevenlabs(text: str, voice_id: str = voice_id) -> tuple[float, float, bytes]:
                url = f"{base}/v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000"
                return await _http_streaming(
                    client,
                    url,
                    headers={"xi-api-key": str(env["BC_ELEVENLABS_API_KEY"])},
                    json={"text": text, "model_id": "eleven_flash_v2_5", "language_code": "es"},
                )

            available_synths[f"elevenlabs-flash/{voice_id[:6]}"] = with_elevenlabs
    return available_synths


async def compare_tts(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    s, env = Settings(), dotenv_values(HERE.parent / ".env")
    async with httpx.AsyncClient(timeout=30) as client:
        todos = tts_providers(s, env, client)
        chosen = {
            k: v
            for k, v in todos.items()
            if not args.providers or k in args.providers or k.split("/")[0] in args.providers
        }
        letters = random.sample("ABCDEFGHJK", len(chosen))
        key = dict(zip(letters, chosen, strict=True))
        (output / "listening").mkdir(parents=True)
        results: dict[str, Any] = {}
        for letter, name in key.items():
            synthesize = chosen[name]
            first_times: list[float] = []
            totals: list[float] = []
            errors: list[str] = []
            try:
                await synthesize("Hola.")  # calentamiento: conexión TLS y carga del modelo
            except Exception as e:  # noqa: BLE001
                errors.append(f"calentamiento: {e}")
            for i, phrase in enumerate(PHRASES):
                for rep in range(args.repetitions):
                    try:
                        primero, total, pcm = await synthesize(phrase)
                    except Exception as e:  # noqa: BLE001
                        errors.append(str(e)[:300])
                        continue
                    first_times.append(primero * 1000)
                    totals.append(total * 1000)
                    if rep == 0:
                        _save_wav(output / "listening" / f"{letter}{i + 1}.wav", pcm, RATE)
            results[name] = {
                "first_audio_ms": _summary(first_times),
                "total_ms": _summary(totals),
                "errors": errors[:3],
            }
            print(f"  {name:<20} primer audio p50 {_fmt(results[name]['first_audio_ms']['p50'])}"
                  f"  total p50 {_fmt(results[name]['total_ms']['p50'])}"
                  f"{'  ERRORES: ' + errors[0] if errors else ''}")  # fmt: skip
    (output / "key.json").write_text(
        json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _listening_page(output, sorted(key))
    return results


def _listening_page(output: Path, letters: list[str]) -> None:
    rows = "".join(
        f"<tr><th>{letter}</th>"
        + "".join(f'<td><audio controls preload="none" src="escucha/{letter}{i + 1}.wav"></audio></td>' for i in range(len(PHRASES)))
        + "</tr>"
        for letter in letters
    )  # fmt: skip
    cab = "".join(f"<th>{f[:40]}…</th>" for f in PHRASES)
    (output / "listening.html").write_text(
        f"""<!doctype html><meta charset="utf-8"><title>Escucha a ciegas</title>
<style>body{{font:15px system-ui;margin:2rem}}td,th{{padding:.4rem;border-bottom:1px solid #ddd;text-align:left}}</style>
<h1>Escucha a ciegas</h1><p>Puntúa cada letra de 1 a 5 (naturalidad, claridad, ¿suena a centralita?)
antes de abrir <code>clave.json</code>.</p><table><tr><th></th>{cab}</tr>{rows}</table>""",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------------------- STT


def _wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _normalize(reference).split(), _normalize(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        previous, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            previous, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, previous + (r != h))
    return d[-1] / len(ref)


class DeepgramCFSession:
    """Nova-3 o Flux de Workers AI por el WebSocket de AI Gateway. Misma interfaz mínima que la
    sesión de AssemblyAI: `open`, `send_audio`, `final()` y `close`."""

    def __init__(self, model: str, s: Settings, gateway: str) -> None:
        params: dict[str, Any] = {
            "model": f"@cf/deepgram/{model}",
            "encoding": "linear16",
            "sample_rate": RATE,
        }
        if model == "nova-3":
            params |= {
                "language": "es",
                "interim_results": "true",
                "endpointing": 300,
                "smart_format": "true",
            }
        self.flux = model == "flux"
        self.url = (
            CF_GATEWAY_WS.format(counts=s.cloudflare_account_id, gateway=gateway)
            + "?"
            + urlencode(params)
        )
        # Sin `keyterm`: por AI Gateway, Nova-3 con keyterm no devuelve ningún resultado (medido
        # 2026-10-10). Queda en desventaja frente a AssemblyAI, que sí recibe el vocabulario.
        self.token = s.cloudflare_api_token
        self._ws: Any = None
        self._finals: asyncio.Queue[str] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._accumulated: list[str] = []

    async def open(self) -> None:
        self._ws = await websockets.connect(
            self.url,
            additional_headers={"cf-aig-authorization": f"Bearer {self.token}"},
            open_timeout=20,
            max_queue=None,
        )
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for raw in self._ws:
            if isinstance(raw, bytes):
                continue
            m = json.loads(raw)
            if self.flux:
                if m.get("event") == "EndOfTurn":
                    await self._finals.put((m.get("transcript") or "").strip())
                continue
            alt = ((m.get("channel") or {}).get("alternatives") or [{}])[0]
            if m.get("is_final") and alt.get("transcript"):
                self._accumulated.append(alt["transcript"])
            if m.get("speech_final") and self._accumulated:
                await self._finals.put(" ".join(self._accumulated).strip())
                self._accumulated = []

    async def send_audio(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def final(self, timeout: float) -> str:
        return await asyncio.wait_for(self._finals.get(), timeout)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()


class AssemblyAISession:
    def __init__(self, s: Settings, keyterms: list[str]) -> None:
        self.session = AssemblyAIStreamingSession(
            s.assemblyai_api_key,
            RATE,
            host=s.assemblyai_streaming_host,
            eot_confidence=s.assemblyai_eot_confidence,
            eot_silence_ms=s.assemblyai_eot_silence_ms,
            max_silence_ms=s.assemblyai_max_silence_ms,
            keyterms=keyterms,
        )

    async def open(self) -> None:
        await self.session.open()
        self._events = self.session.events()

    async def send_audio(self, pcm: bytes) -> None:
        await self.session.send_audio(pcm)

    async def final(self, timeout: float) -> str:
        async def next_final() -> str:
            async for ev in self._events:
                if ev.kind == "final":
                    return ev.text
                if ev.kind == "error":
                    raise RuntimeError(ev.text)
            raise RuntimeError("stream cerrado")

        return await asyncio.wait_for(next_final(), timeout)

    async def close(self) -> None:
        await self.session.close()


def _audio_of(text: str) -> bytes:
    """PCM16 16 kHz de la frase, sin el silencio final (e2e_voz la sintetiza y la cachea)."""
    from e2e_voice import _pcm_without_trailing_silence, synthesize

    pcm, rate = _pcm_without_trailing_silence(synthesize(text))
    return audioop.ratecv(pcm, 2, 1, rate, RATE, None)[0] if rate != RATE else pcm


async def _transcribe(session: Any, pcm: bytes) -> tuple[str, float]:
    """Envía la voz a ritmo real en trozos de 100 ms y luego silencio hasta el turno final."""
    chunk = RATE // 10 * 2
    final_task = asyncio.create_task(session.final(timeout=15))
    for i in range(0, len(pcm), chunk):
        await session.send_audio(pcm[i : i + chunk].ljust(chunk, b"\x00"))
        await asyncio.sleep(0.1)
    voice_end = perf_counter()
    silence = b"\x00" * chunk
    while not final_task.done():
        await session.send_audio(silence)
        await asyncio.sleep(0.1)
    text = await final_task
    return text, (perf_counter() - voice_end) * 1000


async def compare_stt(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    from e2e_voice import SCENARIOS

    s, env = Settings(), dotenv_values(HERE.parent / ".env")
    gateway = env.get("BC_CLOUDFLARE_GATEWAY") or "default"
    phrases = list(dict.fromkeys(t for e in SCENARIOS for t in e["turns"]))[: args.phrases]
    # El mismo vocabulario de fincas que usa la API (Calle Mayor 12, tercero B...)
    keyterms = ["Calle Mayor 12", "Avenida de la Alameda 5", "portal", "tercero", "puerta"]
    factories: dict[str, Callable[[], Any]] = {}
    if s.assemblyai_api_key:
        factories["assemblyai"] = lambda: AssemblyAISession(s, keyterms)
    if s.cloudflare_account_id and s.cloudflare_api_token:
        factories["nova-3"] = lambda: DeepgramCFSession("nova-3", s, str(gateway))
        factories["flux"] = lambda: DeepgramCFSession("flux", s, str(gateway))
    if args.providers:
        factories = {k: v for k, v in factories.items() if k in args.providers}
    audios = {f: _audio_of(f) for f in phrases}
    results: dict[str, Any] = {}
    for name, factory in factories.items():
        latencies: list[float] = []
        wers: list[float] = []
        samples: list[dict[str, Any]] = []
        errors: list[str] = []
        for phrase in phrases:
            await asyncio.sleep(args.pause)  # que el proveedor dé por cerrada la sesión anterior
            session = factory()
            try:
                await session.open()
                text, ms = await _transcribe(session, audios[phrase])
                latencies.append(ms)
                wers.append(_wer(phrase, text))
                samples.append({"expected": phrase, "transcribed": text, "end_turn_ms": round(ms)})
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {str(e)[:200]}")
            finally:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001
                    pass
        results[name] = {
            "end_turn_ms": _summary(latencies),
            "mean_wer": round(sum(wers) / len(wers), 3) if wers else None,
            "samples": samples,
            "errors": errors[:3],
        }
        print(f"  {name:<12} fin de turno p50 {_fmt(results[name]['end_turn_ms']['p50'])}"
              f"  p95 {_fmt(results[name]['end_turn_ms']['p95'])}"
              f"  WER {results[name]['mean_wer']}"
              f"{'  ERRORES: ' + errors[0] if errors else ''}")  # fmt: skip
    return results


# --------------------------------------------------------------------------------------- main


def _fmt(ms: float | None) -> str:
    return "—" if ms is None else f"{ms:.0f} ms"


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("mode", choices=["tts", "stt"])
    ap.add_argument(
        "--providers", type=lambda v: [p.strip() for p in v.split(",") if p.strip()], default=[]
    )
    ap.add_argument(
        "--repetitions", type=int, default=3, help="TTS: veces que se sintetiza cada frase"
    )
    ap.add_argument("--phrases", type=int, default=12, help="STT: cuántas frases distintas enviar")
    ap.add_argument("--pause", type=float, default=2.0, help="STT: segundos entre sesiones")
    args = ap.parse_args()
    output = HERE / "voice_results" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.mode}"
    output.mkdir(parents=True)
    print(f"Comparando {args.mode.upper()}…")
    results = await (compare_tts if args.mode == "tts" else compare_stt)(args, output)
    (output / "results.json").write_text(
        json.dumps({"mode": args.mode, "date": datetime.now().isoformat(), "results": results},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )  # fmt: skip
    print(f"Resultados en {output}")


if __name__ == "__main__":
    asyncio.run(main())
