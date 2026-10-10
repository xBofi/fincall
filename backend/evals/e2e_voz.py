"""Banco de pruebas de voz de punta a punta con audio sintético.

Genera WAV en español con la voz del sistema (Windows SAPI, "Microsoft Helena"), los envía al
WebSocket de voz del backend (AssemblyAI EU -> grafo -> LLM -> redacción) y comprueba el
resultado de la llamada. Sirve para probar cómo habla "una persona normal", no alguien que
sabe qué decir.

Uso (backend en :8000 con BC_ASSEMBLYAI_API_KEY):
    uv run python evals/e2e_voz.py            # todos los escenarios
    uv run python evals/e2e_voz.py --solo natural_todo_junto --verboso
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import hashlib
import json
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import httpx
import websockets

AQUI = Path(__file__).resolve().parent
AUDIO = AQUI / "audio_sintetico"
BASE = "http://localhost:8000"

ESCENARIOS: list[dict[str, Any]] = [
    {
        "nombre": "guiado",
        "turnos": [
            "Calle Mayor doce",
            "sí",
            "tercero B",
            "Marta López",
            "Hay una fuga de agua en el baño",
            "sale con fuerza, del techo",
            "sí",
        ],
        "espera": {
            "resultado": "incidencia_creada",
            "vivienda_id": "finca-mayor-12-u-3B",
            "categoria": "fontaneria",
        },
    },
    {
        "nombre": "natural_todo_junto",
        "turnos": [
            "Hola, buenas, mire, llamo desde la Avenida de la Alameda cinco, portal dos, tercero A. Soy Carmen y tengo una fuga de agua en el baño que está inundando el suelo",
            "sí, eso es",
            "sale con fuerza, creo que del techo",
            "sí, correcto",
        ],
        "espera": {
            "resultado": "incidencia_creada",
            "vivienda_id": "finca-alameda-5-2-3A",
            "categoria": "fontaneria",
        },
    },
    {
        "nombre": "muletillas_y_saludo",
        "turnos": [
            "Hola, buenas.",
            "Sí, vivo en la calle Mayor número doce.",
            "Sí.",
            "Ah, vale, perdón. Pues sí, piso uno, puerta A.",
            "Con Andreu Bofi Pla.",
            "Mira, hay que pintar las paredes del corredor porque no están nada bien.",
            "El corredor del primero, tienen humedad y desconchones.",
            "Sí, es correcto.",
        ],
        "espera": {"resultado": "incidencia_creada", "vivienda_id": "finca-mayor-12-u-1A"},
    },
    {
        "nombre": "portal_ambiguo",
        "turnos": [
            "Avenida de la Alameda cinco",
            "sí",
            "El tercero A es mi vivienda",
            "portal uno",
            "Juan",
            "El ascensor hace un ruido raro",
            "funciona, pero hace ruido al arrancar",
            "sí",
        ],
        "espera": {
            "resultado": "incidencia_creada",
            "vivienda_id": "finca-alameda-5-1-3A",
            "categoria": "ascensor",
        },
    },
    {
        "nombre": "correccion_nombre",
        "turnos": [
            "Calle Mayor doce",
            "sí",
            "segundo B",
            "Pepe",
            "No funciona la luz de la escalera",
            "son todas, no encienden",
            "No, me llamo Pedro García",
            "sí",
        ],
        "espera": {
            "resultado": "incidencia_creada",
            "nombre_declarado~": "Pedro",
            "categoria": "electricidad",
        },
    },
    {
        "nombre": "pregunta_y_humano",
        "turnos": [
            "¿Esta llamada se está grabando?",
            "Calle Mayor doce",
            "sí",
            "Prefiero hablar con una persona, por favor",
        ],
        "espera": {"resultado": "derivada_administrador"},
    },
    {
        "nombre": "vivienda_inexistente_y_ayuda",
        "turnos": [
            "Calle Mayor doce",
            "sí",
            "piso uno, puerta dos",
            "piso uno, puerta B",
            "Ana",
            "Gotea el grifo del patio",
            "una gota cada poco, desde ayer",
            "sí",
        ],
        "espera": {"resultado": "incidencia_creada", "vivienda_id": "finca-mayor-12-u-1B"},
    },
]


def sintetizar(texto: str) -> Path:
    AUDIO.mkdir(exist_ok=True)
    # "-utf8" en el nombre: los WAV anteriores se generaron sin UTF-8 en la entrada de PowerShell
    # y la voz decía "S" por "sí" y "BAO" por "baño"
    ruta = AUDIO / (hashlib.md5(texto.encode("utf-8")).hexdigest()[:12] + "-utf8.wav")
    if ruta.exists():
        return ruta
    guion = (
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'es*' } | Select-Object -First 1; "
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
        f"$s.SetOutputToWaveFile('{ruta}'); $s.Speak([Console]::In.ReadToEnd()); $s.SetOutputToNull()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", guion],
        input=texto.encode("utf-8"),
        check=True,
        capture_output=True,
    )
    return ruta


def _pcm_sin_silencio_final(ruta: Path) -> tuple[bytes, int]:
    w = wave.open(str(ruta), "rb")
    rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    paso = int(rate * 0.02) * 2
    i = len(pcm)
    while i > paso and audioop.rms(pcm[i - paso : i], 2) <= 300:
        i -= paso
    return pcm[:i], rate


async def ejecutar(escenario: dict[str, Any], verboso: bool) -> dict[str, Any]:
    llamada = httpx.post(f"{BASE}/api/llamadas", json={"telefono_llamante": "+34600000000"}).json()
    lid = llamada["llamada_id"]
    rutas = [sintetizar(t) for t in escenario["turnos"]]
    _, rate = _pcm_sin_silencio_final(rutas[0])
    latencias: list[int] = []
    transcripciones: list[str] = []
    ultimo: dict[str, Any] = llamada
    if verboso:
        print(f"    BOT: {' '.join(llamada['mensajes_bot'])}")
    async with websockets.connect(
        f"ws://localhost:8000/api/llamadas/{lid}/voz?sample_rate={rate}", open_timeout=30
    ) as ws:
        await ws.recv()  # listo
        chunk = int(rate * 0.1) * 2
        for texto, ruta in zip(escenario["turnos"], rutas, strict=True):
            pcm, _ = _pcm_sin_silencio_final(ruta)
            for i in range(0, len(pcm), chunk):
                await ws.send(pcm[i : i + chunk])
                await asyncio.sleep(0.1)
            t_fin = time.time()
            respuesta = None
            final = ""
            while time.time() - t_fin < 12 and respuesta is None:
                await ws.send(b"\x00" * chunk)
                try:
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.1))
                        if m["type"] == "final":
                            final = m["texto"]
                        elif m["type"] == "respuesta":
                            respuesta = m
                        elif m["type"] == "error":
                            print("    ERROR", m)
                except TimeoutError:
                    pass
            if respuesta is None:
                return {
                    "nombre": escenario["nombre"],
                    "ok": False,
                    "motivo": f"sin respuesta a {texto!r}",
                }
            latencias.append(int((time.time() - t_fin) * 1000))
            transcripciones.append(final)
            ultimo = respuesta
            if verboso:
                print(f"    VECINO dijo: {texto!r}\n       STT oyó: {final!r}")
                print(
                    f"    BOT [{respuesta['nodo_actual']}, {latencias[-1]} ms]: {' '.join(respuesta['mensajes_bot'])}"
                )
            if respuesta["finalizada"]:
                break
    datos = ultimo["datos"]
    fallos = []
    for k, v in escenario["espera"].items():
        if k.endswith("~"):  # coincidencia laxa (el STT pierde acentos en nombres)
            if not str(datos.get(k[:-1]) or "").lower().startswith(str(v).lower()):
                fallos.append(f"{k}: esperado ~{v!r}, obtenido {datos.get(k[:-1])!r}")
        elif datos.get(k) != v:
            fallos.append(f"{k}: esperado {v!r}, obtenido {datos.get(k)!r}")
    return {
        "nombre": escenario["nombre"],
        "ok": not fallos,
        "motivo": "; ".join(fallos),
        "latencias_ms": latencias,
        "transcripciones": transcripciones,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo")
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    escenarios = [e for e in ESCENARIOS if not args.solo or e["nombre"] == args.solo]
    resultados = []
    for e in escenarios:
        print(f"== {e['nombre']}")
        r = await ejecutar(e, args.verboso)
        resultados.append(r)
        lat = r.get("latencias_ms", [])
        print(
            f"   {'OK ' if r['ok'] else 'FALLO'} {r.get('motivo', '')} | fin_voz->respuesta p50 {sorted(lat)[len(lat) // 2] if lat else '-'} ms max {max(lat) if lat else '-'} ms"
        )
    ok = sum(1 for r in resultados if r["ok"])
    print(f"\n{ok}/{len(resultados)} escenarios correctos")
    (AQUI / "ultimo_e2e_voz.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sys.exit(0 if ok == len(resultados) else 1)


if __name__ == "__main__":
    asyncio.run(main())
