"""Turn classifier: intent, incident category, urgency and work type from one utterance.

A SetFit model (Hugging Face): a multilingual sentence-transformer body fine-tuned with
contrastive pairs, plus one logistic-regression head per task on the same embedding. One encode
answers the four tasks. It is trained by `nlp/train_classifier.py`; the weights are not in git
(the script rebuilds them) and see `nlp/MODEL_CARD.md`.

The heavy libraries (torch, sentence-transformers, scikit-learn) are imported lazily: without the
`nlp` extra the API still starts, and `load_classifier` returns None so the hybrid router stays
off.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

logger = logging.getLogger(__name__)

TASKS: tuple[str, ...] = ("intent", "category", "urgency", "work_type")


@dataclass(frozen=True)
class Label:
    value: str | None
    confidence: float = 0.0


@dataclass(frozen=True)
class TurnPrediction:
    intent: Label
    category: Label = field(default_factory=lambda: Label(None))
    urgency: Label = field(default_factory=lambda: Label(None))
    work_type: Label = field(default_factory=lambda: Label(None))
    ms: int = 0

    def get(self, task: str) -> Label:
        label: Label = getattr(self, task)
        return label


class TurnClassifier(Protocol):
    name: str

    def predict(self, text: str) -> TurnPrediction: ...

    def predict_many(self, texts: list[str]) -> list[TurnPrediction]: ...


class Encoder(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


class Head(Protocol):
    classes_: Any

    def predict_proba(self, x: Any) -> Any: ...


class SetFitTurnClassifier:
    """Body (sentence-transformer) + one sklearn head per task. Works with any encoder and heads
    that follow the sklearn/sentence-transformers interfaces, so the tests use tiny fakes."""

    def __init__(self, encoder: Encoder, heads: dict[str, Head], name: str = "setfit") -> None:
        self.encoder = encoder
        self.heads = heads
        self.name = name

    def predict_many(self, texts: list[str]) -> list[TurnPrediction]:
        if not texts:
            return []
        t0 = perf_counter()
        embeddings = self.encoder.encode(texts, normalize_embeddings=True)
        probs = {task: head.predict_proba(embeddings) for task, head in self.heads.items()}
        ms = int((perf_counter() - t0) * 1000 / len(texts))
        out: list[TurnPrediction] = []
        for i in range(len(texts)):
            labels: dict[str, Label] = {}
            for task, head in self.heads.items():
                row = [float(p) for p in probs[task][i]]
                best = max(range(len(row)), key=row.__getitem__)
                labels[task] = Label(str(head.classes_[best]), round(row[best], 4))
            out.append(
                TurnPrediction(
                    intent=labels.get("intent", Label(None)),
                    category=labels.get("category", Label(None)),
                    urgency=labels.get("urgency", Label(None)),
                    work_type=labels.get("work_type", Label(None)),
                    ms=ms,
                )
            )
        return out

    def predict(self, text: str) -> TurnPrediction:
        return self.predict_many([text])[0]

    @classmethod
    def load(cls, directory: str | Path, device: str | None = None) -> SetFitTurnClassifier:
        """Loads `body/` (sentence-transformers) and `heads.joblib` written by the training
        script. Raises ImportError without the `nlp` extra, FileNotFoundError without weights."""
        import joblib
        from sentence_transformers import SentenceTransformer

        path = Path(directory)
        if not (path / "heads.joblib").exists():
            raise FileNotFoundError(f"No classifier weights in {path}: run nlp/train_classifier.py")
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        encoder = SentenceTransformer(str(path / "body"), device=device or "cpu")
        heads: dict[str, Head] = joblib.load(path / "heads.joblib")
        return cls(encoder, heads, name=f"setfit/{meta.get('base_model', 'unknown')}")


def load_classifier(directory: str | Path, device: str | None = None) -> TurnClassifier | None:
    """The classifier, or None (logged) if the extra or the weights are missing."""
    try:
        return SetFitTurnClassifier.load(directory, device=device)
    except (ImportError, FileNotFoundError, OSError) as e:
        logger.warning("Turn classifier not available (%s): the LLM interprets every turn", e)
        return None
