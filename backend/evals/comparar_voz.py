"""Compara proveedores de voz para elegir con datos: síntesis (TTS) y transcripción (STT).

TTS: para frases reales del asistente mide el tiempo hasta el primer audio (lo que se ganaría
reproduciendo en streaming) y el tiempo total (lo que paga hoy el pipeline, que sintetiza la
frase entera antes de enviarla). Guarda los audios con letras anónimas para una escucha a
ciegas: `escucha.html` no dice qué proveedor es cada letra; `clave.json` sí.

STT: envía las frases de `e2e_voz.py` (voz sintética de Windows) a ritmo real, como haría una
llamada, y mide la latencia de fin de turno (último audio con voz → transcripción final) y la
tasa de error por palabra (WER) contra el texto original.

Proveedores (se omiten los que no tienen credenciales en backend/.env):
- TTS: piper (local), melotts y aura-2-es (Cloudflare Workers AI), elevenlabs
  (BC_ELEVENLABS_API_KEY + BC_ELEVENLABS_VOZ; BC_ELEVENLABS_URL para el endpoint de residencia UE).
- STT: assemblyai (endpoint UE), nova-3 y flux (Workers AI a través de AI Gateway,
  BC_CLOUDFLARE_GATEWAY, por defecto "default").

Uso:
    uv run python evals/comparar_voz.py tts --repeticiones 3
    uv run python evals/comparar_voz.py stt --proveedores assemblyai,nova-3
Resultados en evals/voz_resultados/<fecha>/ (ignorado por git: contiene audio).
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
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

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "src"))
sys.path.insert(0, str(AQUI))

from building_call.graph.nodos import _normalizar  # noqa: E402
from building_call.settings import Settings  # noqa: E402
from building_call.stt_streaming import SesionStreamingAssemblyAI  # noqa: E402
from building_call.telefonia.tts import SintetizadorPiper  # noqa: E402

TASA = 16000
CF_API = "https://api.cloudflare.com/client/v4/accounts/{cuenta}/ai/run/{modelo}"
CF_GATEWAY_WS = "wss://gateway.ai.cloudflare.com/v1/{cuenta}/{gateway}/workers-ai"

# Frases reales del asistente: corta, pregunta, resumen largo y cierre largo
FRASES = [
    "He identificado Calle Mayor 12. ¿Es correcto?",
    "Gracias. ¿Cuál es su vivienda? Indíqueme portal si lo hay, piso y puerta.",
    "Le resumo: Marta, 3º B. Fuga de agua en el baño que está inundando el suelo. "
    "Lo he clasificado como fontanería, con urgencia alta. ¿Es correcto?",
    "He registrado su incidencia. Por su urgencia aviso ahora mismo a Fontanería Pérez, que se "
    "pondrá en contacto con usted lo antes posible. Gracias por su llamada, hasta luego.",
]


def _p(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    orden = sorted(valores)
    return orden[max(0, min(len(orden) - 1, round(q * (len(orden) - 1))))]


def _resumen(valores: list[float]) -> dict[str, Any]:
    return {"n": len(valores), "p50": _p(valores, 0.5), "p95": _p(valores, 0.95)}


def _guardar_wav(ruta: Path, pcm: bytes, tasa: int) -> None:
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tasa)
        w.writeframes(pcm)


async def _a_pcm(audio: bytes, ffmpeg: str = "ffmpeg") -> bytes:
    """Cualquier formato (mp3, wav...) → PCM16 mono 16 kHz."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(TASA),
        "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    pcm, err = await proc.communicate(audio)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err.decode(errors='ignore')[:200]}")
    return pcm


# --------------------------------------------------------------------------------------- TTS

# Cada proveedor devuelve (primer_audio_s, total_s, pcm16_16k)
Sintesis = Callable[[str], Awaitable[tuple[float, float, bytes]]]


async def _http_en_streaming(
    cliente: httpx.AsyncClient, url: str, **kw: Any
) -> tuple[float, float, bytes]:
    t0 = perf_counter()
    primero: float | None = None
    trozos: list[bytes] = []
    async with cliente.stream("POST", url, **kw) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {(await r.aread())[:300]!r}")
        async for trozo in r.aiter_bytes():
            if primero is None and trozo:
                primero = perf_counter() - t0
            trozos.append(trozo)
    total = perf_counter() - t0
    return primero or total, total, b"".join(trozos)


def proveedores_tts(
    s: Settings, env: dict[str, str | None], cliente: httpx.AsyncClient
) -> dict[str, Sintesis]:
    disponibles: dict[str, Sintesis] = {}

    piper = SintetizadorPiper(s.piper_bin, s.piper_modelo)
    if piper.disponible():

        async def con_piper(texto: str) -> tuple[float, float, bytes]:
            # Sin la caché del sintetizador: se mide la síntesis, no una consulta a un dict
            t0 = perf_counter()
            if piper._paquete_disponible():
                pcm = await asyncio.to_thread(piper._sintetizar_en_proceso, texto)
            else:
                pcm = await piper._sintetizar_subproceso(texto)
            total = perf_counter() - t0
            if piper.tasa != TASA:
                pcm, _ = audioop.ratecv(pcm, 2, 1, int(piper.tasa), TASA, None)
            return total, total, pcm

        disponibles["piper"] = con_piper

    if s.cloudflare_account_id and s.cloudflare_api_token:
        cabeceras = {"Authorization": f"Bearer {s.cloudflare_api_token}"}

        async def con_melotts(texto: str) -> tuple[float, float, bytes]:
            url = CF_API.format(cuenta=s.cloudflare_account_id, modelo="@cf/myshell-ai/melotts")
            t0 = perf_counter()
            r = await cliente.post(url, headers=cabeceras, json={"prompt": texto, "lang": "es"})
            r.raise_for_status()
            mp3 = base64.b64decode(r.json()["result"]["audio"])
            total = perf_counter() - t0  # devuelve JSON con todo el audio: primero = total
            return total, total, await _a_pcm(mp3)

        disponibles["melotts"] = con_melotts

        for voz in ("javier", "carina"):

            async def con_aura(texto: str, voz: str = voz) -> tuple[float, float, bytes]:
                url = CF_API.format(cuenta=s.cloudflare_account_id, modelo="@cf/deepgram/aura-2-es")
                primero, total, audio = await _http_en_streaming(
                    cliente, url, headers=cabeceras, json={"text": texto, "speaker": voz}
                )
                return primero, total, await _a_pcm(audio)

            disponibles[f"aura-2-es/{voz}"] = con_aura

    if env.get("BC_ELEVENLABS_API_KEY") and env.get("BC_ELEVENLABS_VOZ"):
        base = (env.get("BC_ELEVENLABS_URL") or "https://api.elevenlabs.io").rstrip("/")

        async def con_elevenlabs(texto: str) -> tuple[float, float, bytes]:
            url = f"{base}/v1/text-to-speech/{env['BC_ELEVENLABS_VOZ']}/stream?output_format=pcm_16000"
            return await _http_en_streaming(
                cliente,
                url,
                headers={"xi-api-key": str(env["BC_ELEVENLABS_API_KEY"])},
                json={"text": texto, "model_id": "eleven_flash_v2_5", "language_code": "es"},
            )

        disponibles["elevenlabs-flash"] = con_elevenlabs
    return disponibles


async def comparar_tts(args: argparse.Namespace, salida: Path) -> dict[str, Any]:
    s, env = Settings(), dotenv_values(AQUI.parent / ".env")
    async with httpx.AsyncClient(timeout=30) as cliente:
        todos = proveedores_tts(s, env, cliente)
        elegidos = {
            k: v
            for k, v in todos.items()
            if not args.proveedores or k.split("/")[0] in args.proveedores
        }
        letras = random.sample("ABCDEFGHJK", len(elegidos))
        clave = dict(zip(letras, elegidos, strict=True))
        (salida / "escucha").mkdir(parents=True)
        resultados: dict[str, Any] = {}
        for letra, nombre in clave.items():
            sintetizar = elegidos[nombre]
            primeros: list[float] = []
            totales: list[float] = []
            errores: list[str] = []
            try:
                await sintetizar("Hola.")  # calentamiento: conexión TLS y carga del modelo
            except Exception as e:  # noqa: BLE001
                errores.append(f"calentamiento: {e}")
            for i, frase in enumerate(FRASES):
                for rep in range(args.repeticiones):
                    try:
                        primero, total, pcm = await sintetizar(frase)
                    except Exception as e:  # noqa: BLE001
                        errores.append(str(e)[:300])
                        continue
                    primeros.append(primero * 1000)
                    totales.append(total * 1000)
                    if rep == 0:
                        _guardar_wav(salida / "escucha" / f"{letra}{i + 1}.wav", pcm, TASA)
            resultados[nombre] = {
                "primer_audio_ms": _resumen(primeros),
                "total_ms": _resumen(totales),
                "errores": errores[:3],
            }
            print(f"  {nombre:<20} primer audio p50 {_fmt(resultados[nombre]['primer_audio_ms']['p50'])}"
                  f"  total p50 {_fmt(resultados[nombre]['total_ms']['p50'])}"
                  f"{'  ERRORES: ' + errores[0] if errores else ''}")  # fmt: skip
    (salida / "clave.json").write_text(
        json.dumps(clave, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _pagina_escucha(salida, sorted(clave))
    return resultados


def _pagina_escucha(salida: Path, letras: list[str]) -> None:
    filas = "".join(
        f"<tr><th>{letra}</th>"
        + "".join(f'<td><audio controls preload="none" src="escucha/{letra}{i + 1}.wav"></audio></td>' for i in range(len(FRASES)))
        + "</tr>"
        for letra in letras
    )  # fmt: skip
    cab = "".join(f"<th>{f[:40]}…</th>" for f in FRASES)
    (salida / "escucha.html").write_text(
        f"""<!doctype html><meta charset="utf-8"><title>Escucha a ciegas</title>
<style>body{{font:15px system-ui;margin:2rem}}td,th{{padding:.4rem;border-bottom:1px solid #ddd;text-align:left}}</style>
<h1>Escucha a ciegas</h1><p>Puntúa cada letra de 1 a 5 (naturalidad, claridad, ¿suena a centralita?)
antes de abrir <code>clave.json</code>.</p><table><tr><th></th>{cab}</tr>{filas}</table>""",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------------------- STT


def _wer(referencia: str, hipotesis: str) -> float:
    ref, hip = _normalizar(referencia).split(), _normalizar(hipotesis).split()
    if not ref:
        return 0.0 if not hip else 1.0
    d = list(range(len(hip) + 1))
    for i, r in enumerate(ref, 1):
        previo, d[0] = d[0], i
        for j, h in enumerate(hip, 1):
            previo, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, previo + (r != h))
    return d[-1] / len(ref)


class SesionDeepgramCF:
    """Nova-3 o Flux de Workers AI por el WebSocket de AI Gateway. Misma interfaz mínima que la
    sesión de AssemblyAI: `abrir`, `enviar_audio`, `final()` y `cerrar`."""

    def __init__(self, modelo: str, s: Settings, gateway: str) -> None:
        params: dict[str, Any] = {
            "model": f"@cf/deepgram/{modelo}",
            "encoding": "linear16",
            "sample_rate": TASA,
        }
        if modelo == "nova-3":
            params |= {
                "language": "es",
                "interim_results": "true",
                "endpointing": 300,
                "smart_format": "true",
            }
        self.flux = modelo == "flux"
        self.url = (
            CF_GATEWAY_WS.format(cuenta=s.cloudflare_account_id, gateway=gateway)
            + "?"
            + urlencode(params)
        )
        # Sin `keyterm`: por AI Gateway, Nova-3 con keyterm no devuelve ningún resultado (medido
        # 2026-10-10). Queda en desventaja frente a AssemblyAI, que sí recibe el vocabulario.
        self.token = s.cloudflare_api_token
        self._ws: Any = None
        self._finales: asyncio.Queue[str] = asyncio.Queue()
        self._lector: asyncio.Task[None] | None = None
        self._acumulado: list[str] = []

    async def abrir(self) -> None:
        self._ws = await websockets.connect(
            self.url,
            additional_headers={"cf-aig-authorization": f"Bearer {self.token}"},
            open_timeout=20,
            max_queue=None,
        )
        self._lector = asyncio.create_task(self._leer())

    async def _leer(self) -> None:
        async for crudo in self._ws:
            if isinstance(crudo, bytes):
                continue
            m = json.loads(crudo)
            if self.flux:
                if m.get("event") == "EndOfTurn":
                    await self._finales.put((m.get("transcript") or "").strip())
                continue
            alt = ((m.get("channel") or {}).get("alternatives") or [{}])[0]
            if m.get("is_final") and alt.get("transcript"):
                self._acumulado.append(alt["transcript"])
            if m.get("speech_final") and self._acumulado:
                await self._finales.put(" ".join(self._acumulado).strip())
                self._acumulado = []

    async def enviar_audio(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def final(self, timeout: float) -> str:
        return await asyncio.wait_for(self._finales.get(), timeout)

    async def cerrar(self) -> None:
        if self._lector:
            self._lector.cancel()
        if self._ws is not None:
            await self._ws.close()


class SesionAssemblyAI:
    def __init__(self, s: Settings, keyterms: list[str]) -> None:
        self.sesion = SesionStreamingAssemblyAI(
            s.assemblyai_api_key,
            TASA,
            host=s.assemblyai_streaming_host,
            eot_confianza=s.assemblyai_eot_confianza,
            eot_silencio_ms=s.assemblyai_eot_silencio_ms,
            max_silencio_ms=s.assemblyai_max_silencio_ms,
            keyterms=keyterms,
        )

    async def abrir(self) -> None:
        await self.sesion.abrir()
        self._eventos = self.sesion.eventos()

    async def enviar_audio(self, pcm: bytes) -> None:
        await self.sesion.enviar_audio(pcm)

    async def final(self, timeout: float) -> str:
        async def siguiente_final() -> str:
            async for ev in self._eventos:
                if ev.tipo == "final":
                    return ev.texto
                if ev.tipo == "error":
                    raise RuntimeError(ev.texto)
            raise RuntimeError("stream cerrado")

        return await asyncio.wait_for(siguiente_final(), timeout)

    async def cerrar(self) -> None:
        await self.sesion.cerrar()


def _audio_de(texto: str) -> bytes:
    """PCM16 16 kHz de la frase, sin el silencio final (e2e_voz la sintetiza y la cachea)."""
    from e2e_voz import _pcm_sin_silencio_final, sintetizar

    pcm, tasa = _pcm_sin_silencio_final(sintetizar(texto))
    return audioop.ratecv(pcm, 2, 1, tasa, TASA, None)[0] if tasa != TASA else pcm


async def _transcribir(sesion: Any, pcm: bytes) -> tuple[str, float]:
    """Envía la voz a ritmo real en trozos de 100 ms y luego silencio hasta el turno final."""
    trozo = TASA // 10 * 2
    tarea_final = asyncio.create_task(sesion.final(timeout=15))
    for i in range(0, len(pcm), trozo):
        await sesion.enviar_audio(pcm[i : i + trozo].ljust(trozo, b"\x00"))
        await asyncio.sleep(0.1)
    fin_voz = perf_counter()
    silencio = b"\x00" * trozo
    while not tarea_final.done():
        await sesion.enviar_audio(silencio)
        await asyncio.sleep(0.1)
    texto = await tarea_final
    return texto, (perf_counter() - fin_voz) * 1000


async def comparar_stt(args: argparse.Namespace, salida: Path) -> dict[str, Any]:
    from e2e_voz import ESCENARIOS

    s, env = Settings(), dotenv_values(AQUI.parent / ".env")
    gateway = env.get("BC_CLOUDFLARE_GATEWAY") or "default"
    frases = list(dict.fromkeys(t for e in ESCENARIOS for t in e["turnos"]))[: args.frases]
    # El mismo vocabulario de fincas que usa la API (Calle Mayor 12, tercero B...)
    keyterms = ["Calle Mayor 12", "Avenida de la Alameda 5", "portal", "tercero", "puerta"]
    fabricas: dict[str, Callable[[], Any]] = {}
    if s.assemblyai_api_key:
        fabricas["assemblyai"] = lambda: SesionAssemblyAI(s, keyterms)
    if s.cloudflare_account_id and s.cloudflare_api_token:
        fabricas["nova-3"] = lambda: SesionDeepgramCF("nova-3", s, str(gateway))
        fabricas["flux"] = lambda: SesionDeepgramCF("flux", s, str(gateway))
    if args.proveedores:
        fabricas = {k: v for k, v in fabricas.items() if k in args.proveedores}
    audios = {f: _audio_de(f) for f in frases}
    resultados: dict[str, Any] = {}
    for nombre, fabrica in fabricas.items():
        latencias: list[float] = []
        wers: list[float] = []
        muestras: list[dict[str, Any]] = []
        errores: list[str] = []
        for frase in frases:
            await asyncio.sleep(args.pausa)  # que el proveedor dé por cerrada la sesión anterior
            sesion = fabrica()
            try:
                await sesion.abrir()
                texto, ms = await _transcribir(sesion, audios[frase])
                latencias.append(ms)
                wers.append(_wer(frase, texto))
                muestras.append({"esperado": frase, "transcrito": texto, "fin_turno_ms": round(ms)})
            except Exception as e:  # noqa: BLE001
                errores.append(f"{type(e).__name__}: {str(e)[:200]}")
            finally:
                try:
                    await sesion.cerrar()
                except Exception:  # noqa: BLE001
                    pass
        resultados[nombre] = {
            "fin_turno_ms": _resumen(latencias),
            "wer_medio": round(sum(wers) / len(wers), 3) if wers else None,
            "muestras": muestras,
            "errores": errores[:3],
        }
        print(f"  {nombre:<12} fin de turno p50 {_fmt(resultados[nombre]['fin_turno_ms']['p50'])}"
              f"  p95 {_fmt(resultados[nombre]['fin_turno_ms']['p95'])}"
              f"  WER {resultados[nombre]['wer_medio']}"
              f"{'  ERRORES: ' + errores[0] if errores else ''}")  # fmt: skip
    return resultados


# --------------------------------------------------------------------------------------- main


def _fmt(ms: float | None) -> str:
    return "—" if ms is None else f"{ms:.0f} ms"


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("modo", choices=["tts", "stt"])
    ap.add_argument(
        "--proveedores", type=lambda v: [p.strip() for p in v.split(",") if p.strip()], default=[]
    )
    ap.add_argument(
        "--repeticiones", type=int, default=3, help="TTS: veces que se sintetiza cada frase"
    )
    ap.add_argument("--frases", type=int, default=12, help="STT: cuántas frases distintas enviar")
    ap.add_argument("--pausa", type=float, default=2.0, help="STT: segundos entre sesiones")
    args = ap.parse_args()
    salida = AQUI / "voz_resultados" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.modo}"
    salida.mkdir(parents=True)
    print(f"Comparando {args.modo.upper()}…")
    resultados = await (comparar_tts if args.modo == "tts" else comparar_stt)(args, salida)
    (salida / "resultados.json").write_text(
        json.dumps({"modo": args.modo, "fecha": datetime.now().isoformat(), "resultados": resultados},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )  # fmt: skip
    print(f"Resultados en {salida}")


if __name__ == "__main__":
    asyncio.run(main())
