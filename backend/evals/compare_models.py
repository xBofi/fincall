"""Compara modelos Ollama en las dos tareas LLM del voicebot: interpretación de turno y
clasificación. Mide exactitud y latencia (p50/p95) para decidir el modelo de producción.

Uso:
    uv run python evals/compare_models.py --modelos qwen3.5:4b,qwen2.5:1.5b --repeticiones 1
    uv run python evals/compare_models.py --modelos vertex:gemini-3.5-flash-lite,cf:@cf/qwen/qwen3.8-27b
    sb run -- uv run python evals/compare_models.py --models openai:gpt-6-luna,bedrock:eu.anthropic.claude-haiku-5-5
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from building_call import i18n  # noqa: E402
from building_call.domain.config import (  # noqa: E402
    DEFAULTS,
    UNCLASSIFIABLE_CATEGORY,
    URGENCIES,
)
from building_call.graph.nodes import (  # noqa: E402
    _normalize,
    _normalize_door,
    _normalize_floor,
)
from building_call.llm import wire  # noqa: E402
from building_call.llm.interpretation import interpret  # noqa: E402
from building_call.llm.ollama import OllamaModel  # noqa: E402
from building_call.llm.prompts import (  # noqa: E402
    classification_schema,
    classification_system_prompt,
)
from building_call.llm.vertex import VertexModel  # noqa: E402
from building_call.llm.workers_ai import WorkersAIModel  # noqa: E402

HERE = Path(__file__).resolve().parent


def _read(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (HERE / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _p(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    order = sorted(values)
    k = max(0, min(len(order) - 1, round(q * (len(order) - 1))))
    return order[k]


def _matches(key: str, expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):  # cualquiera de los valores vale (null = sin dato)
        return any(_matches(key, e, actual) for e in expected)
    if expected == "*":
        return actual not in (None, "")
    if expected is None:
        return actual in (None, "")
    if isinstance(expected, bool):
        return actual is expected
    if actual in (None, ""):
        return False
    if key == "address":
        # "Elm Avenue 5" is the same address as "5 Elm Avenue": every word of it must be there
        return set(_normalize(str(expected)).split()) <= set(_normalize(str(actual)).split()) or (
            _normalize(str(actual)) in _normalize(str(expected))
        )
    if key == "floor":
        return _normalize_floor(str(actual)) == str(expected)
    if key == "door":
        return _normalize_door(str(actual)) == str(expected)
    if key == "entrance":
        return _normalize_floor(str(actual)) == str(expected)
    if key == "name":
        return _normalize(str(expected)).split()[0] in _normalize(str(actual))
    return str(expected).lower() == str(actual).lower()


async def evaluate_interpretation(
    llm: Any, cases: list[dict[str, Any]], reps: int
) -> dict[str, Any]:
    hits = total = 0
    timings: list[float] = []
    failures: list[str] = []
    for case in cases:
        for _ in range(reps):
            try:
                # Con "catalogo" el turno también clasifica, como en el grafo (una sola llamada)
                catalog = (
                    [*DEFAULTS.category_catalog, UNCLASSIFIABLE_CATEGORY]
                    if case.get("catalog")
                    else None
                )
                r = await interpret(
                    llm,
                    case["question"],
                    case["utterance"],
                    case["node"],
                    catalog=catalog,
                    heard_text=case.get("heard_text"),  # el vecino cortó el mensaje (ledger de entrega)
                    equipment_list=[tuple(e) for e in case["equipment_list"]] if case.get("equipment_list") else None,
                    # What the call already knows (building, apartment), as the graph passes it
                    known=case.get("known") if not os.environ.get("EVAL_NO_KNOWN") else None,
                )
            except Exception as e:  # noqa: BLE001
                failures.append(f"ERROR {case['utterance'][:40]!r}: {e}")
                total += len(case["expected"])
                continue
            timings.append(r.llm_ms)
            for key, expected in case["expected"].items():
                total += 1
                actual = getattr(r, key)
                if _matches(key, expected, actual):
                    hits += 1
                else:
                    failures.append(
                        f"{case['utterance'][:45]!r} {key}: esperado={expected!r} obtenido={actual!r}"
                    )
    return {
        "accuracy": hits / max(total, 1),
        "n": total,
        "p50_ms": _p(timings, 0.5),
        "p95_ms": _p(timings, 0.95),
        "failures": failures,
    }


async def evaluate_classification(llm: Any, cases: list[dict[str, Any]], reps: int) -> dict[str, Any]:
    cat_ok = urg_ok = total = 0
    timings: list[float] = []
    failures: list[str] = []
    catalog = [*DEFAULTS.category_catalog, UNCLASSIFIABLE_CATEGORY]
    for case in cases:
        for _ in range(reps):
            t0 = perf_counter()
            try:
                r = wire.read(
                    await llm.extract(
                        classification_system_prompt(catalog),
                        case["request"],
                        classification_schema(catalog),
                    )
                )
            except Exception as e:  # noqa: BLE001
                failures.append(f"ERROR {case['request'][:40]!r}: {e}")
                total += 1
                continue
            timings.append((perf_counter() - t0) * 1000)
            total += 1
            # Como `classify_problem`: sin categoría o fuera del catálogo es no_clasificable
            if r.get("category") not in catalog:
                r["category"] = UNCLASSIFIABLE_CATEGORY
            # Con varias categorías válidas (el portero no tiene una clara) vale cualquiera
            valid_categories = (
                case["category"] if isinstance(case["category"], list) else [case["category"]]
            )
            if r.get("category") in valid_categories:
                cat_ok += 1
            else:
                failures.append(
                    f"{case['request'][:45]!r} categoria: esperado={case['category']} obtenido={r.get('category')}"
                )
            if r.get("urgency") in case["urgencies"]:
                urg_ok += 1
            elif r.get("urgency") in URGENCIES:
                failures.append(
                    f"{case['request'][:45]!r} urgencia: esperado={case['urgencies']} obtenido={r.get('urgency')}"
                )
    return {
        "category": cat_ok / max(total, 1),
        "urgency": urg_ok / max(total, 1),
        "n": total,
        "p50_ms": _p(timings, 0.5),
        "p95_ms": _p(timings, 0.95),
        "failures": failures,
    }


def _read_env() -> dict[str, str]:
    """Lee backend/.env (solo para las credenciales de proveedores externos)."""
    import re

    env: dict[str, str] = {}
    path = HERE.parent / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def _create_llm(name: str, ollama_url: str) -> Any:
    """`cf:@cf/qwen/qwen3.8-27b` -> Workers AI; `vertex:gemini-3.5-flash-lite` -> Vertex AI (UE);
    cualquier otro nombre -> Ollama local."""
    if name.startswith("vertex:"):
        env = _read_env()
        return VertexModel(
            env.get("BC_VERTEX_PROJECT", ""),
            name[7:],
            location=env.get("BC_VERTEX_LOCATION", "eu"),
            credentials_path=str(HERE.parent / path)
            if (path := env.get("BC_VERTEX_CREDENTIALS"))
            else "",
            timeout_s=60,
        )
    if name.startswith(("openai:", "bedrock:")):
        # Keys from the environment (`sb run -- ...` with SecretBroker), never printed
        import os

        env = {**_read_env(), **os.environ}
        provider, model = name.split(":", 1)
        if provider == "openai":
            from building_call.llm.openai import OpenAIModel

            return OpenAIModel(
                env.get("BC_OPENAI_API_KEY", ""),
                model,
                base_url=env.get("BC_OPENAI_URL", "https://api.openai.com"),
                reasoning_effort=env.get("BC_OPENAI_REASONING_EFFORT") or None,
                timeout_s=60,
            )
        from building_call.llm.bedrock import BedrockModel

        return BedrockModel(
            env.get("BC_BEDROCK_API_KEY", ""),
            model,
            region=env.get("BC_BEDROCK_REGION", "eu-west-1"),
            timeout_s=120,  # the first call with a new schema compiles it (docs: up to 120 s)
        )
    if name.startswith("cf:"):
        env = _read_env()
        return WorkersAIModel(
            env.get("BC_CLOUDFLARE_ACCOUNT_ID", ""),
            env.get("BC_CLOUDFLARE_API_TOKEN", ""),
            name[3:],
            timeout_s=60,
        )
    return OllamaModel(ollama_url, name, timeout_s=60)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--repetitions", type=int, default=1)
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--language",
        default="es",
        help="es: gold_*.jsonl with the Spanish prompts; en: gold_*_en.jsonl with the English ones",
    )
    args = ap.parse_args()
    suffix = "" if args.language == "es" else f"_{args.language}"
    cases_i = _read(f"gold_interpretation{suffix}.jsonl")
    cases_c = _read(f"gold_classification{suffix}.jsonl")
    # Prompts, parsing and the floor/door comparison in the gold set's language
    stack = contextlib.ExitStack()
    stack.enter_context(i18n.use(args.language))
    print(
        f"{'model':<18} {'interp.exact':>12} {'p50':>6} {'p95':>6} | {'clas.cat':>8} {'clas.urg':>8} {'p50':>6} {'p95':>6}"
    )
    summary = []
    for model in args.models.split(","):
        llm = _create_llm(model.strip(), args.url)
        # Calentamiento: carga el modelo en GPU
        try:
            await llm.extract(
                "Devuelve JSON.",
                "hola",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )
        except Exception as e:  # noqa: BLE001
            print(f"{model:<44} no disponible: {e}")
            continue
        ri = await evaluate_interpretation(llm, cases_i, args.repetitions)
        rc = await evaluate_classification(llm, cases_c, args.repetitions)
        print(
            f"{model:<44} {ri['accuracy']:>11.0%} {ri['p50_ms']:>6.0f} {ri['p95_ms']:>6.0f} | {rc['category']:>7.0%} {rc['urgency']:>8.0%} {rc['p50_ms']:>6.0f} {rc['p95_ms']:>6.0f}"
        )
        if args.verbose:
            for f in ri["failures"] + rc["failures"]:
                print("   -", f)
        summary.append(
            {
                "model": model,
                "interpretation": {k: v for k, v in ri.items() if k != "failures"},
                "classification": {k: v for k, v in rc.items() if k != "failures"},
            }
        )
    (HERE / "last_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
