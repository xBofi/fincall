"""Accuracy and per-class / macro F1 for the NLP evals (no scikit-learn needed).

The gold sets accept several right answers in some rows (`"urgencies": ["low", "medium"]`, two
categories for the door entry system). For F1 each row gets ONE reference label: the predicted
label if it is among the accepted ones, otherwise the first accepted one. Accuracy counts a hit
whenever the prediction is accepted, as `evals/compare_models.py` does.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def reference(accepted: Any, predicted: str | None) -> str:
    options = accepted if isinstance(accepted, list) else [accepted]
    options = [o if o is not None else "continue" for o in options]
    return predicted if predicted is not None and predicted in options else str(options[0])


def per_class_f1(gold: Sequence[str], predicted: Sequence[str | None]) -> dict[str, float]:
    labels = sorted(set(gold))
    out: dict[str, float] = {}
    for label in labels:
        tp = sum(1 for g, p in zip(gold, predicted, strict=True) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, predicted, strict=True) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, predicted, strict=True) if g == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        out[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return out


def score(accepted: Sequence[Any], predicted: Sequence[str | None]) -> dict[str, Any]:
    """{accuracy, macro_f1, per_class, n} of a list of accepted answers vs predictions."""
    gold = [reference(a, p) for a, p in zip(accepted, predicted, strict=True)]
    hits = sum(1 for g, p in zip(gold, predicted, strict=True) if g == p)
    f1 = per_class_f1(gold, predicted)
    return {
        "n": len(gold),
        "accuracy": hits / len(gold) if gold else 0.0,
        "macro_f1": sum(f1.values()) / len(f1) if f1 else 0.0,
        "per_class": f1,
    }


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    order = sorted(values)
    k = max(0, min(len(order) - 1, round(q * (len(order) - 1))))
    return float(order[k])
