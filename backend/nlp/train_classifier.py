"""Trains the turn classifier (SetFit) and writes it to `nlp/models/turn-classifier/`.

Reproducible: fixed seed, fixed stratified split, the config and the SHA-256 of the training
data go to `meta.json` next to the weights. The weights are NOT in git (.gitignore): this script
rebuilds them (about a minute on an RTX 4070 SUPER, ~10 min on CPU).

Method (SetFit, Tunstall et al. 2022): (1) contrastive fine-tuning of a multilingual
sentence-transformer on pairs of training sentences with the same / different label; the label
for the pairs combines intent and category ("intent:human", "cat:plumbing"), so the embedding
space separates both; (2) one logistic-regression head per task (intent, category, urgency,
work type) on the fine-tuned embeddings. One encode then answers the four tasks.

Usage:
    uv sync --extra nlp
    uv run python nlp/data/build_dataset.py --seed 13
    uv run python nlp/train_classifier.py
    uv run python nlp/train_classifier.py --base-model sentence-transformers/paraphrase-multilingual-mpnet-base-v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from building_call.nlp.classifier import TASKS, SetFitTurnClassifier  # noqa: E402
from building_call.nlp.scoring import score  # noqa: E402

DEFAULTS: dict[str, Any] = {
    "base_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "seed": 13,
    "val_fraction": 0.15,
    "num_iterations": 20,  # contrastive pairs per sentence
    "num_epochs": 1,
    "batch_size": 32,
    "body_learning_rate": 2e-5,
    "head_c": 4.0,
}


def pair_label(row: dict[str, Any]) -> str:
    return f"cat:{row['category']}" if row.get("category") else f"intent:{row['intent']}"


def split(rows: list[dict[str, Any]], fraction: float, seed: int) -> tuple[list[Any], list[Any]]:
    """Stratified by the pair label, deterministic."""
    rng = random.Random(seed)
    by_label: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_label.setdefault(pair_label(row), []).append(row)
    train, val = [], []
    for label in sorted(by_label):
        group = by_label[label][:]
        rng.shuffle(group)
        k = max(1, round(len(group) * fraction)) if len(group) >= 4 else 0
        val += group[:k]
        train += group[k:]
    return train, val


def set_seed(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def train_body(rows: list[dict[str, Any]], cfg: dict[str, Any]) -> Any:
    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments

    model = SetFitModel.from_pretrained(cfg["base_model"])
    args = TrainingArguments(
        batch_size=cfg["batch_size"],
        num_epochs=cfg["num_epochs"],
        num_iterations=cfg["num_iterations"],
        body_learning_rate=cfg["body_learning_rate"],
        seed=cfg["seed"],
        report_to="none",
    )
    data = Dataset.from_dict(
        {"text": [r["text"] for r in rows], "label": [pair_label(r) for r in rows]}
    )
    Trainer(model=model, args=args, train_dataset=data).train()
    return model.model_body


def train_heads(body: Any, rows: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    heads: dict[str, Any] = {}
    for task in TASKS:
        labelled = [r for r in rows if r.get(task)]
        x = body.encode([r["text"] for r in labelled], normalize_embeddings=True)
        head = LogisticRegression(
            C=cfg["head_c"], max_iter=5000, class_weight="balanced", random_state=cfg["seed"]
        )
        head.fit(x, [r[task] for r in labelled])
        heads[task] = head
    return heads


def evaluate_val(classifier: SetFitTurnClassifier, rows: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = classifier.predict_many([r["text"] for r in rows])
    out: dict[str, Any] = {}
    for task in TASKS:
        pairs = [
            (r[task], p.get(task).value)
            for r, p in zip(rows, predictions, strict=True)
            if r.get(task)
        ]
        out[task] = score([g for g, _ in pairs], [p for _, p in pairs])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data" / "train.jsonl"))
    ap.add_argument("--out", default=str(HERE / "models" / "turn-classifier"))
    for key, value in DEFAULTS.items():
        ap.add_argument(f"--{key.replace('_', '-')}", type=type(value), default=value)
    args = ap.parse_args()
    cfg = {k: getattr(args, k) for k in DEFAULTS}
    data_path = Path(args.data)
    raw = data_path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    set_seed(cfg["seed"])
    train, val = split(rows, cfg["val_fraction"], cfg["seed"])
    print(f"train {len(train)} / val {len(val)} rows; base {cfg['base_model']}")

    t0 = perf_counter()
    body = train_body(train, cfg)
    heads = train_heads(body, train, cfg)
    train_s = perf_counter() - t0
    classifier = SetFitTurnClassifier(body, heads, name=f"setfit/{cfg['base_model']}")
    val_scores = evaluate_val(classifier, val)
    for task, s in val_scores.items():
        print(f"val {task:<10} acc {s['accuracy']:.1%}  macro-F1 {s['macro_f1']:.3f}  (n={s['n']})")

    # Final model: the same recipe on train + val (the val numbers above stay the estimate)
    set_seed(cfg["seed"])
    body = train_body(rows, cfg)
    heads = train_heads(body, rows, cfg)

    import joblib

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    body.save(str(out / "body"))
    joblib.dump(heads, out / "heads.joblib")
    meta = {
        **cfg,
        "data": data_path.name,
        "data_sha256": hashlib.sha256(raw).hexdigest(),
        "rows": len(rows),
        "labels": {t: dict(Counter(r[t] for r in rows if r.get(t))) for t in TASKS},
        "validation": {t: {k: v for k, v in s.items()} for t, s in val_scores.items()},
        "train_seconds_split_run": round(train_s, 1),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved -> {out}")


if __name__ == "__main__":
    main()
