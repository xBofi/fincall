"""LLM-only vs classifier-only vs hybrid on the gold sets, plus spaCy vs regex entities.

Tasks (test data = the gold sets, never trained on):
- intent      `gold_interpretation*.jsonl` (rows without an expected intent count as `continue`)
- category    `gold_classification*.jsonl`
- urgency     `gold_classification*.jsonl` (any of the accepted urgencies is right)

Systems:
- llm         the project's prompts: `interpret` for intents, the classification prompt for
              category/urgency (as `compare_models.py`)
- classifier  the SetFit turn classifier alone (`nlp/train_classifier.py`)
- hybrid      `HybridRouter` exactly as in the graph (threshold, entity gate); when it declines,
              the LLM answer of the same row is used and both latencies are added

Cost per 1,000 calls = cost per LLM request x LLM requests per call. Requests per call =
`--llm-turns-per-call` (default 4: the caller turns of a typical call that are not deterministic)
x the share of turns that reach the LLM in that system. Tokens are the provider's usage when it
reports it, else characters / 4. Prices are USD per 1M tokens (input, output) from the official
pricing pages, read 2026-11-24; local models cost 0 (energy not counted).

Usage:
    uv run python evals/compare_nlp.py --llm qwen3.5:4b
    uv run python evals/compare_nlp.py --llm vertex:gemini-3.5-flash-lite --threshold 0.9
    uv run python evals/compare_nlp.py --entities-only
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_models import _create_llm, _matches  # noqa: E402

from building_call.domain.config import DEFAULTS, UNCLASSIFIABLE_CATEGORY  # noqa: E402
from building_call.llm import wire  # noqa: E402
from building_call.llm.interpretation import interpret  # noqa: E402
from building_call.llm.prompts import (  # noqa: E402
    classification_schema,
    classification_system_prompt,
)
from building_call.nlp.scoring import percentile, score  # noqa: E402

HERE = Path(__file__).resolve().parent
CATALOG = [*DEFAULTS.category_catalog, UNCLASSIFIABLE_CATEGORY]

#: USD per 1M tokens (input, output). Sources: aistudio.google.com/docs/pricing (Gemini
#: Developer API; the Vertex page did not render), developers.openai.com/api/docs/pricing
PRICES: dict[str, tuple[float, float]] = {
    "vertex:gemini-3.5-flash-lite": (0.30, 2.50),
    "openai:gpt-6-luna": (0.10, 0.50),
    "openai:gpt-5.4-nano": (0.20, 1.25),
}


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _price(llm_name: str) -> tuple[float, float]:
    if ":" not in llm_name:
        return (0.0, 0.0)  # Ollama, local
    return PRICES.get(llm_name, (float("nan"), float("nan")))


# --------------------------------------------------------------------------- LLM runs


async def run_llm(
    llm: Any, intents: list[dict[str, Any]], classes: list[dict[str, Any]]
) -> dict[str, Any]:
    intent_rows: list[dict[str, Any]] = []
    for case in intents:
        catalog = CATALOG if case.get("catalog") else None
        t0 = perf_counter()
        try:
            r = await interpret(
                llm, case["question"], case["utterance"], case["node"], catalog=catalog
            )
            value: str | None = r.intent
        except Exception as e:  # noqa: BLE001
            print(f"  LLM error {case['utterance'][:40]!r}: {e}")
            value = None
        ms = (perf_counter() - t0) * 1000
        intent_rows.append(
            {"value": value, "ms": ms, "tokens": _tokens(llm, case["utterance"], 1800)}
        )
    class_rows: list[dict[str, Any]] = []
    for case in classes:
        t0 = perf_counter()
        try:
            out = wire.read(
                await llm.extract(
                    classification_system_prompt(CATALOG),
                    case["request"],
                    classification_schema(CATALOG),
                )
            )
        except Exception as e:  # noqa: BLE001
            print(f"  LLM error {case['request'][:40]!r}: {e}")
            out = {}
        ms = (perf_counter() - t0) * 1000
        category = (
            out.get("category") if out.get("category") in CATALOG else UNCLASSIFIABLE_CATEGORY
        )
        class_rows.append(
            {"category": category, "urgency": out.get("urgency"), "ms": ms,
             "tokens": _tokens(llm, case["request"], 1400)}
        )  # fmt: skip
    return {"intent": intent_rows, "classification": class_rows}


def _tokens(llm: Any, user: str, system_chars: int) -> tuple[int, int]:
    usage = getattr(llm, "last_usage", None)
    if usage:
        return (usage["input"], usage["output"])
    return ((system_chars + len(user)) // 4, 15)


# --------------------------------------------------------------------------- scoring


def _intent_expected(case: dict[str, Any]) -> Any:
    expected = case["expected"].get("intent", "continue")
    return (
        [e if e is not None else "continue" for e in expected]
        if isinstance(expected, list)
        else expected
    )


def evaluate(
    name: str,
    intents: list[dict[str, Any]],
    classes: list[dict[str, Any]],
    intent_pred: list[dict[str, Any]],
    class_pred: list[dict[str, Any]],
    price: tuple[float, float],
    llm_turns_per_call: float,
) -> dict[str, Any]:
    s_intent = score([_intent_expected(c) for c in intents], [p["value"] for p in intent_pred])
    s_cat = score([c["category"] for c in classes], [p["category"] for p in class_pred])
    s_urg = score([c["urgencies"] for c in classes], [p["urgency"] for p in class_pred])
    preds = intent_pred + class_pred
    ms = [p["ms"] for p in preds]
    llm_share = sum(1 for p in preds if p.get("llm")) / len(preds)
    llm_preds = [p for p in preds if p.get("llm")]
    if llm_preds:
        tin = sum(p["tokens"][0] for p in llm_preds) / len(llm_preds)
        tout = sum(p["tokens"][1] for p in llm_preds) / len(llm_preds)
        per_request = (tin * price[0] + tout * price[1]) / 1e6
    else:
        per_request = 0.0
    cost = per_request * llm_turns_per_call * llm_share * 1000
    return {
        "system": name,
        "intent": s_intent,
        "category": s_cat,
        "urgency": s_urg,
        "p50_ms": percentile(ms, 0.5),
        "p95_ms": percentile(ms, 0.95),
        "llm_share": llm_share,
        "cost_per_1000_calls_usd": cost,
    }


# --------------------------------------------------------------------------- entities


ENTITY_FIELDS = ("address", "entrance", "floor", "door", "name")


def compare_entities(cases: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    from building_call.nlp.entities import extract_entities, regex_entities, spacy_entities

    systems = {"regex": regex_entities, "spacy": spacy_entities, "combined": extract_entities}
    out: dict[str, dict[str, dict[str, float]]] = {}
    for sname, fn in systems.items():
        out[sname] = {}
        found = [fn(c["utterance"]) for c in cases]
        for field in ENTITY_FIELDS:
            relevant = [
                (c["expected"][field], f.get(field)) for c, f in zip(cases, found, strict=True)
                if field in c["expected"] and c["expected"][field] not in (None, "*")
            ]  # fmt: skip
            extracted = [(e, a) for e, a in relevant if a]
            correct = sum(1 for e, a in extracted if _matches(field, e, a))
            spurious = sum(
                1 for c, f in zip(cases, found, strict=True)
                if f.get(field) and field not in c["expected"]
            )  # fmt: skip
            out[sname][field] = {
                "n": len(relevant),
                "recall": correct / len(relevant) if relevant else 0.0,
                "precision": correct / len(extracted) if extracted else 0.0,
                "spurious": spurious,
            }
    return out


# --------------------------------------------------------------------------- main


def _table(results: list[dict[str, Any]]) -> str:
    head = (
        "| System | Intent acc | Intent macro-F1 | Category acc | Category macro-F1 | "
        "Urgency acc | Urgency macro-F1 | p50 ms | p95 ms | Turns to LLM | USD / 1,000 calls |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = "".join(
        f"| {r['system']} | {r['intent']['accuracy']:.1%} | {r['intent']['macro_f1']:.3f} | "
        f"{r['category']['accuracy']:.1%} | {r['category']['macro_f1']:.3f} | "
        f"{r['urgency']['accuracy']:.1%} | {r['urgency']['macro_f1']:.3f} | "
        f"{r['p50_ms']:.0f} | {r['p95_ms']:.0f} | {r['llm_share']:.0%} | "
        f"{r['cost_per_1000_calls_usd']:.2f} |\n"
        for r in results
    )
    return head + rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--llm",
        default="qwen3.5:4b",
        help="as compare_models.py: ollama name, vertex:, openai:, bedrock:",
    )
    ap.add_argument("--model-dir", default=str(HERE.parent / "nlp" / "models" / "turn-classifier"))
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument(
        "--thresholds", default="", help="extra thresholds to sweep, e.g. 0.7,0.8,0.9,0.95"
    )
    ap.add_argument("--gold", action="append", default=[], help="extra gold files (e.g. English)")
    ap.add_argument("--llm-turns-per-call", type=float, default=4.0)
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--entities-only", action="store_true")
    ap.add_argument("--out", default=str(HERE / "last_nlp_result.json"))
    args = ap.parse_args()

    gold_paths = [
        HERE / "gold_interpretation.jsonl",
        HERE / "gold_classification.jsonl",
        *map(Path, args.gold),
    ]
    intents = [r for p in gold_paths if "interpretation" in p.name for r in _read(p)]
    classes = [r for p in gold_paths if "classification" in p.name for r in _read(p)]
    print(f"gold: {len(intents)} intent rows, {len(classes)} classification rows")

    entities = compare_entities(
        [r for p in gold_paths if "interpretation" in p.name for r in _read(p)]
    )
    print("\nEntities (gold_interpretation): recall / precision / spurious")
    for sname, fields in entities.items():
        print(f"  {sname:<9}" + "  ".join(
            f"{f}: {v['recall']:.0%}/{v['precision']:.0%}/{v['spurious']}" for f, v in fields.items()
        ))  # fmt: skip
    if args.entities_only:
        return

    from building_call.nlp.classifier import SetFitTurnClassifier
    from building_call.nlp.entities import extract_entities
    from building_call.nlp.hybrid import HybridRouter

    clf = SetFitTurnClassifier.load(args.model_dir)
    clf.predict("warm up")
    intent_clf = [clf.predict(c["utterance"]) for c in intents]
    class_clf = [clf.predict(c["request"]) for c in classes]

    llm = _create_llm(args.llm, args.url)
    try:
        await llm.extract(
            "Devuelve JSON.", "hola", {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        )
    except Exception as e:  # noqa: BLE001
        print(f"LLM {args.llm} not available: {e}")
        return
    print(f"LLM {args.llm}: running {len(intents) + len(classes)} requests...")
    llm_out = await run_llm(llm, intents, classes)
    price = _price(args.llm)

    results = [
        evaluate(
            f"LLM only ({args.llm})", intents, classes,
            [{**p, "llm": True} for p in llm_out["intent"]],
            [{**p, "llm": True} for p in llm_out["classification"]],
            price, args.llm_turns_per_call,
        ),
        evaluate(
            f"Classifier only ({clf.name.split('/')[-1]})", intents, classes,
            [{"value": p.intent.value, "ms": p.ms} for p in intent_clf],
            [{"category": p.category.value, "urgency": p.urgency.value, "ms": p.ms} for p in class_clf],
            price, args.llm_turns_per_call,
        ),
    ]  # fmt: skip
    thresholds = [args.threshold, *[float(t) for t in args.thresholds.split(",") if t]]
    for threshold in thresholds:
        router = HybridRouter(clf, threshold, extract_entities)
        hi: list[dict[str, Any]] = []
        for case, p, lp in zip(intents, intent_clf, llm_out["intent"], strict=True):
            interp = router.interpret(
                case["utterance"], case["node"], CATALOG if case.get("catalog") else None, p
            )
            if interp is not None:
                hi.append({"value": interp.intent, "ms": p.ms})
            else:
                hi.append({**lp, "ms": lp["ms"] + p.ms, "llm": True})
        hc: list[dict[str, Any]] = []
        for p, lp in zip(class_clf, llm_out["classification"], strict=True):
            sure = (
                router._sure(p, "category", "urgency", "work_type") and p.category.value in CATALOG
            )
            if sure:
                hc.append({"category": p.category.value, "urgency": p.urgency.value, "ms": p.ms})
            else:
                hc.append({**lp, "ms": lp["ms"] + p.ms, "llm": True})
        results.append(
            evaluate(
                f"Hybrid @ {threshold}", intents, classes, hi, hc, price, args.llm_turns_per_call
            )
        )

    print()
    print(_table(results))
    for r in results:
        print(f"\n{r['system']} per-class F1")
        for task in ("intent", "category", "urgency"):
            print(
                f"  {task}: " + ", ".join(f"{k} {v:.2f}" for k, v in r[task]["per_class"].items())
            )
    Path(args.out).write_text(
        json.dumps({"llm": args.llm, "classifier": clf.name, "results": results, "entities": entities},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )  # fmt: skip


if __name__ == "__main__":
    asyncio.run(main())
