"""Compara modelos Ollama en las dos tareas LLM del voicebot: interpretación de turno y
clasificación. Mide exactitud y latencia (p50/p95) para decidir el modelo de producción.

Uso:
    uv run python evals/comparar_modelos.py --modelos qwen3.5:4b,qwen2.5:1.5b --repeticiones 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from building_call.domain.configuracion import (  # noqa: E402
    CATEGORIA_NO_CLASIFICABLE,
    DEFAULTS,
    URGENCIAS,
)
from building_call.graph.nodos import (  # noqa: E402
    _normalizar,
    _normalizar_piso,
    _normalizar_puerta,
)
from building_call.llm.interpretacion import interpretar  # noqa: E402
from building_call.llm.ollama import ModeloOllama  # noqa: E402
from building_call.llm.prompts import esquema_clasificacion, sistema_clasificacion  # noqa: E402
from building_call.llm.workers_ai import ModeloWorkersAI  # noqa: E402

AQUI = Path(__file__).resolve().parent


def _leer(nombre: str) -> list[dict[str, Any]]:
    return [
        json.loads(linea)
        for linea in (AQUI / nombre).read_text(encoding="utf-8").splitlines()
        if linea.strip()
    ]


def _p(valores: list[float], q: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = max(0, min(len(orden) - 1, round(q * (len(orden) - 1))))
    return orden[k]


def _coincide(clave: str, esperado: Any, obtenido: Any) -> bool:
    if esperado == "*":
        return obtenido not in (None, "")
    if esperado is None:
        return obtenido in (None, "")
    if isinstance(esperado, bool):
        return obtenido is esperado
    if obtenido in (None, ""):
        return False
    if clave == "direccion":
        return _normalizar(str(esperado)) in _normalizar(str(obtenido)) or _normalizar(
            str(obtenido)
        ) in _normalizar(str(esperado))
    if clave == "piso":
        return _normalizar_piso(str(obtenido)) == str(esperado)
    if clave == "puerta":
        return _normalizar_puerta(str(obtenido)) == str(esperado)
    if clave == "portal":
        return _normalizar_piso(str(obtenido)) == str(esperado)
    if clave == "nombre":
        return _normalizar(str(esperado)).split()[0] in _normalizar(str(obtenido))
    return str(esperado).lower() == str(obtenido).lower()


async def evaluar_interpretacion(
    llm: Any, casos: list[dict[str, Any]], reps: int
) -> dict[str, Any]:
    aciertos = total = 0
    tiempos: list[float] = []
    fallos: list[str] = []
    for caso in casos:
        for _ in range(reps):
            try:
                r = await interpretar(llm, caso["pregunta"], caso["entrada"], caso["nodo"])
            except Exception as e:  # noqa: BLE001
                fallos.append(f"ERROR {caso['entrada'][:40]!r}: {e}")
                total += len(caso["esperado"])
                continue
            tiempos.append(r.llm_ms)
            for clave, esperado in caso["esperado"].items():
                total += 1
                obtenido = getattr(r, clave)
                if _coincide(clave, esperado, obtenido):
                    aciertos += 1
                else:
                    fallos.append(
                        f"{caso['entrada'][:45]!r} {clave}: esperado={esperado!r} obtenido={obtenido!r}"
                    )
    return {
        "exactitud": aciertos / max(total, 1),
        "n": total,
        "p50_ms": _p(tiempos, 0.5),
        "p95_ms": _p(tiempos, 0.95),
        "fallos": fallos,
    }


async def evaluar_clasificacion(llm: Any, casos: list[dict[str, Any]], reps: int) -> dict[str, Any]:
    cat_ok = urg_ok = total = 0
    tiempos: list[float] = []
    fallos: list[str] = []
    catalogo = [*DEFAULTS.catalogo_categorias, CATEGORIA_NO_CLASIFICABLE]
    for caso in casos:
        for _ in range(reps):
            t0 = perf_counter()
            try:
                r = await llm.extraer(
                    sistema_clasificacion(catalogo),
                    caso["solicitud"],
                    esquema_clasificacion(catalogo),
                )
            except Exception as e:  # noqa: BLE001
                fallos.append(f"ERROR {caso['solicitud'][:40]!r}: {e}")
                total += 1
                continue
            tiempos.append((perf_counter() - t0) * 1000)
            total += 1
            if r.get("categoria") == caso["categoria"]:
                cat_ok += 1
            else:
                fallos.append(
                    f"{caso['solicitud'][:45]!r} categoria: esperado={caso['categoria']} obtenido={r.get('categoria')}"
                )
            if r.get("urgencia") in caso["urgencias"]:
                urg_ok += 1
            elif r.get("urgencia") in URGENCIAS:
                fallos.append(
                    f"{caso['solicitud'][:45]!r} urgencia: esperado={caso['urgencias']} obtenido={r.get('urgencia')}"
                )
    return {
        "categoria": cat_ok / max(total, 1),
        "urgencia": urg_ok / max(total, 1),
        "n": total,
        "p50_ms": _p(tiempos, 0.5),
        "p95_ms": _p(tiempos, 0.95),
        "fallos": fallos,
    }


def _leer_env() -> dict[str, str]:
    """Lee backend/.env (solo para las credenciales de proveedores externos)."""
    import re

    env: dict[str, str] = {}
    ruta = AQUI.parent / ".env"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)", linea)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def _crear_llm(nombre: str, url_ollama: str) -> Any:
    """`cf:@cf/qwen/qwen3.8-27b` -> Workers AI; cualquier otro nombre -> Ollama local."""
    if nombre.startswith("cf:"):
        env = _leer_env()
        return ModeloWorkersAI(
            env.get("BC_CLOUDFLARE_ACCOUNT_ID", ""),
            env.get("BC_CLOUDFLARE_API_TOKEN", ""),
            nombre[3:],
            timeout_s=60,
        )
    return ModeloOllama(url_ollama, nombre, timeout_s=60)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelos", required=True)
    ap.add_argument("--repeticiones", type=int, default=1)
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    casos_i = _leer("gold_interpretacion.jsonl")
    casos_c = _leer("gold_clasificacion.jsonl")
    print(
        f"{'modelo':<18} {'interp.exact':>12} {'p50':>6} {'p95':>6} | {'clas.cat':>8} {'clas.urg':>8} {'p50':>6} {'p95':>6}"
    )
    resumen = []
    for modelo in args.modelos.split(","):
        llm = _crear_llm(modelo.strip(), args.url)
        # Calentamiento: carga el modelo en GPU
        try:
            await llm.extraer(
                "Devuelve JSON.",
                "hola",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )
        except Exception as e:  # noqa: BLE001
            print(f"{modelo:<44} no disponible: {e}")
            continue
        ri = await evaluar_interpretacion(llm, casos_i, args.repeticiones)
        rc = await evaluar_clasificacion(llm, casos_c, args.repeticiones)
        print(
            f"{modelo:<44} {ri['exactitud']:>11.0%} {ri['p50_ms']:>6.0f} {ri['p95_ms']:>6.0f} | {rc['categoria']:>7.0%} {rc['urgencia']:>8.0%} {rc['p50_ms']:>6.0f} {rc['p95_ms']:>6.0f}"
        )
        if args.verboso:
            for f in ri["fallos"] + rc["fallos"]:
                print("   -", f)
        resumen.append(
            {
                "modelo": modelo,
                "interpretacion": {k: v for k, v in ri.items() if k != "fallos"},
                "clasificacion": {k: v for k, v in rc.items() if k != "fallos"},
            }
        )
    (AQUI / "ultimo_resultado.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
