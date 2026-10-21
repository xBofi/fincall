"""Banco de voz de punta a punta por LiveKit, con los mismos escenarios que `e2e_voz.py`.

Un participante sintético entra en la sala, habla con la voz del sistema (WAV de Windows SAPI)
y escucha al agente (`building_call.voz.agente`). Mide `respuesta_ms` como la consola: del fin de
la voz del vecino al primer audio del bot. El escenario `interrupcion` corta el resumen y
comprueba el ledger de entrega.

Uso (API en :8000 con BC_AGENTE_SECRETO y LiveKit, agente arrancado, LiveKit en :7880):
    uv run --extra voz python evals/e2e_livekit.py
    uv run --extra voz python evals/e2e_livekit.py --solo interrupcion --verboso
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
from e2e_voz import ESCENARIOS, _pcm_sin_silencio_final, sintetizar
from livekit import api, rtc

AQUI = Path(__file__).resolve().parent
BASE = "http://localhost:8000"
URL_LIVEKIT = os.environ.get("BC_LIVEKIT_URL", "ws://localhost:7880")
CLAVE = os.environ.get("BC_LIVEKIT_API_KEY", "devkey")
SECRETO_LK = os.environ.get("BC_LIVEKIT_API_SECRET", "secret")
SECRETO_AGENTE = os.environ.get("BC_AGENTE_SECRETO", "")

TASA = 48000
TRAMA_MS = 10
UMBRAL_VOZ_BOT = 500  # RMS del audio del agente que cuenta como voz
SILENCIO_FIN_BOT_S = 0.9  # el bot ha terminado de hablar tras este silencio

INTERRUPCION: dict[str, Any] = {
    "nombre": "interrupcion",
    # Con la pregunta de detalle, el resumen llega tras el quinto turno
    "turnos": [
        "Calle Mayor doce",
        "sí",
        "primero A",
        "Gotea el grifo del patio",
        "una gota cada poco",
    ],
    # Tras el último turno el bot lee el resumen: se le corta y se dice "sí" a lo que no oyó
    "cortar_con": "sí, sí",
    "cortar_tras_s": 1.2,
}


class OidoBot:
    """Escucha al agente y anota cuándo empieza y termina de sonar."""

    def __init__(self) -> None:
        self.inicio_voz: float | None = None
        self.ultima_voz = 0.0
        self.hablando = False

    async def escuchar(self, track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=TASA, num_channels=1):
            datos = bytes(ev.frame.data)
            if audioop.rms(datos, 2) > UMBRAL_VOZ_BOT:
                ahora = time.monotonic()
                if not self.hablando:
                    self.hablando = True
                    self.inicio_voz = ahora
                self.ultima_voz = ahora
            elif self.hablando and time.monotonic() - self.ultima_voz > SILENCIO_FIN_BOT_S:
                self.hablando = False

    async def esperar_fin(self, maximo_s: float = 30) -> None:
        t0 = time.monotonic()
        while (self.hablando or self.inicio_voz is None) and time.monotonic() - t0 < maximo_s:
            await asyncio.sleep(0.05)


class Boca:
    """Micrófono sintético: trozos de 10 ms en tiempo real, silencio cuando no habla."""

    def __init__(self) -> None:
        self.fuente = rtc.AudioSource(TASA, 1)
        self._cola: list[bytes] = []
        self._muestras = TASA * TRAMA_MS // 1000

    def decir(self, pcm: bytes) -> None:
        paso = self._muestras * 2
        self._cola += [pcm[i : i + paso].ljust(paso, b"\0") for i in range(0, len(pcm), paso)]

    def callada(self) -> bool:
        return not self._cola

    async def bombear(self) -> None:
        silencio = b"\0" * self._muestras * 2
        siguiente = time.monotonic()
        while True:
            datos = self._cola.pop(0) if self._cola else silencio
            await self.fuente.capture_frame(rtc.AudioFrame(datos, TASA, 1, self._muestras))
            siguiente += TRAMA_MS / 1000
            await asyncio.sleep(max(0, siguiente - time.monotonic()))


def _a_48k(ruta: Path) -> bytes:
    pcm, tasa = _pcm_sin_silencio_final(ruta)
    return audioop.ratecv(pcm, 2, 1, tasa, TASA, None)[0]


async def ejecutar(
    escenario: dict[str, Any], http: httpx.AsyncClient, verboso: bool
) -> dict[str, Any]:
    vista = (await http.post("/api/llamadas", json={"telefono_llamante": "+34600000000"})).json()
    lid = vista["llamada_id"]
    if verboso:
        print(f"    llamada {lid}")
    sala = f"banco-{lid}"
    token = (
        api.AccessToken(CLAVE, SECRETO_LK)
        .with_identity(f"vecino-{lid}")
        .with_grants(api.VideoGrants(room_join=True, room=sala))
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name="fincall", metadata=json.dumps({"llamada_id": lid})
                    )
                ]
            )
        )
        .to_jwt()
    )
    room = rtc.Room()
    oido, boca = OidoBot(), Boca()
    tareas: list[asyncio.Task[Any]] = []

    @room.on("track_subscribed")
    def _pista(track: rtc.Track, *_: Any) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            tareas.append(asyncio.ensure_future(oido.escuchar(track)))

    await room.connect(URL_LIVEKIT, token)
    pista = rtc.LocalAudioTrack.create_audio_track("microfono", boca.fuente)
    await room.local_participant.publish_track(
        pista, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    tareas.append(asyncio.ensure_future(boca.bombear()))
    latencias: list[int] = []
    try:
        await oido.esperar_fin(40)  # el saludo
        for texto in escenario["turnos"]:
            await oido.esperar_fin()
            boca.decir(_a_48k(sintetizar(texto)))
            while not boca.callada():
                await asyncio.sleep(0.01)
            t_fin = time.monotonic()
            oido.inicio_voz = None
            while oido.inicio_voz is None and time.monotonic() - t_fin < 15:
                await asyncio.sleep(0.01)
            if oido.inicio_voz is None:
                return {
                    "nombre": escenario["nombre"],
                    "ok": False,
                    "motivo": f"sin voz tras {texto!r}",
                }
            latencias.append(int((oido.inicio_voz - t_fin) * 1000))
        if corte := escenario.get("cortar_con"):
            await asyncio.sleep(escenario["cortar_tras_s"])  # el resumen está sonando
            boca.decir(_a_48k(sintetizar(corte)))
            await asyncio.sleep(6)
            v = (await http.get(f"/api/llamadas/{lid}")).json()
            return _comprobar_interrupcion(escenario, v, latencias, verboso)
        await oido.esperar_fin()
        await asyncio.sleep(2)  # que el agente informe de la última entrega
    finally:
        for t in tareas:
            t.cancel()
        await room.disconnect()
    final = (await http.get(f"/api/llamadas/{lid}")).json()
    if verboso:
        _transcripcion(escenario, final, latencias)
    datos = final["datos"]
    fallos = []
    for k, v in escenario["espera"].items():
        if k.endswith("~"):
            if not str(datos.get(k[:-1]) or "").lower().startswith(str(v).lower()):
                fallos.append(f"{k}: esperado ~{v!r}, obtenido {datos.get(k[:-1])!r}")
        elif datos.get(k) != v:
            fallos.append(f"{k}: esperado {v!r}, obtenido {datos.get(k)!r}")
    return {
        "nombre": escenario["nombre"],
        "ok": not fallos,
        "motivo": "; ".join(fallos),
        "latencias_ms": latencias,
    }


def _transcripcion(escenario: dict[str, Any], v: dict[str, Any], latencias: list[int]) -> None:
    print(f"    guion: {escenario['turnos']}")
    print(f"    latencias: {latencias}")
    for h in v["historial"]:
        print(f"    {h['rol'].upper():6} {h['texto']}")


def _comprobar_interrupcion(
    escenario: dict[str, Any], v: dict[str, Any], latencias: list[int], verboso: bool
) -> dict[str, Any]:
    traza = [t["accion"] for t in v["traza"]]
    if verboso:
        _transcripcion(escenario, v, latencias)
        print(f"    entrega: {json.dumps(v.get('entrega_ultima'), ensure_ascii=False)[:400]}")
    fallos = []
    if v["datos"]["resultado"] == "incidencia_creada":
        fallos.append("el sí sobre un resumen no oído confirmó la incidencia")
    if "confirmacion_sin_oir" not in traza and "repetir_no_oido" not in traza:
        fallos.append(f"no se repitió el resumen (traza: {traza[-4:]})")
    return {
        "nombre": escenario["nombre"],
        "ok": not fallos,
        "motivo": "; ".join(fallos),
        "latencias_ms": latencias,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo")
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    if not SECRETO_AGENTE:
        sys.exit("Falta BC_AGENTE_SECRETO (el banco entra en la API como el agente)")
    escenarios = [
        e for e in [*ESCENARIOS, INTERRUPCION] if not args.solo or e["nombre"] == args.solo
    ]
    resultados = []
    async with httpx.AsyncClient(
        base_url=BASE, headers={"Authorization": f"Bearer {SECRETO_AGENTE}"}, timeout=30
    ) as http:
        for e in escenarios:
            print(f"== {e['nombre']}")
            r = await ejecutar(e, http, args.verboso)
            resultados.append(r)
            lat = sorted(r.get("latencias_ms", []))
            print(
                f"   {'OK ' if r['ok'] else 'FALLO'} {r.get('motivo', '')} | respuesta p50 "
                f"{lat[len(lat) // 2] if lat else '-'} ms max {lat[-1] if lat else '-'} ms"
            )
    todas = sorted(x for r in resultados for x in r.get("latencias_ms", []))
    if todas:
        p95 = todas[min(len(todas) - 1, round(0.95 * (len(todas) - 1)))]
        print(
            f"\nrespuesta_ms global: p50 {todas[len(todas) // 2]} ms, p95 {p95} ms (n={len(todas)})"
        )
    ok = sum(1 for r in resultados if r["ok"])
    print(f"{ok}/{len(resultados)} escenarios correctos")
    (AQUI / "ultimo_e2e_livekit.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sys.exit(0 if ok == len(resultados) else 1)


if __name__ == "__main__":
    asyncio.run(main())
