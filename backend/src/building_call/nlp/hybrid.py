"""Hybrid routing: the turn classifier answers first; the LLM only when it is not sure.

Off by default (`BC_NLP_HYBRID=false`) until `evals/compare_nlp.py` proves it on the gold sets.
The classifier cannot extract free data (an address, a corrected floor, a question to answer),
so it only replaces the LLM in two shapes of turn, both with every needed label at or above the
threshold AND no entity found in the utterance (spaCy + regex):

1. A global intent with nothing else: "quiero hablar con una persona", "¿me lo repite?",
   "¿cuánto tardan?", "adiós". `correct` never: the corrected value is free data.
2. In `collect_request`, a plain description of the problem: category, urgency and work type
   come from the classifier and the utterance itself is the problem (the node already keeps the
   literal description).

`classify_problem` uses `classify` the same way: confident category, urgency and work type, and
no equipment to choose (choosing the affected equipment needs the LLM).

The safety rules of the graph do not change: handing over or hanging up still require the
caller's explicit words (`_asks_for_person`, `_says_goodbye` in the nodes).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from building_call.llm.interpretation import Interpretation
from building_call.nlp.classifier import TurnClassifier, TurnPrediction

#: Intents the classifier may decide alone (no data to extract)
GLOBAL_INTENTS = frozenset({"repeat", "human", "hang_up", "out_of_scope", "question"})
#: Nodes where the caller describes the problem
PROBLEM_NODES = frozenset({"collect_request"})

EntityExtractor = Callable[[str], dict[str, str]]


@dataclass(frozen=True)
class Classification:
    category: str
    urgency: str
    work_type: str
    ms: int


class HybridRouter:
    def __init__(
        self,
        classifier: TurnClassifier,
        threshold: float = 0.8,
        entities: EntityExtractor | None = None,
    ) -> None:
        self.classifier = classifier
        self.threshold = threshold
        self.entities = entities or _no_entities
        self.name = f"hybrid/{classifier.name}@{threshold}"

    def _sure(self, prediction: TurnPrediction, *tasks: str) -> bool:
        return all(
            prediction.get(t).value is not None and prediction.get(t).confidence >= self.threshold
            for t in tasks
        )

    def interpret(
        self,
        utterance: str,
        node: str,
        catalog: list[str] | None = None,
        prediction: TurnPrediction | None = None,
    ) -> Interpretation | None:
        """The interpretation without the LLM, or None when the LLM must interpret the turn."""
        text = utterance.strip()
        if not text or self.entities(text):
            return None
        p = prediction or self.classifier.predict(text)
        if not self._sure(p, "intent"):
            return None
        intent = p.intent.value
        if intent in GLOBAL_INTENTS:
            return Interpretation(
                intent=intent,  # type: ignore[arg-type]
                question=text if intent == "question" else None,
                llm_ms=p.ms,
                origin="classifier",
            )
        if (
            intent == "continue"
            and node in PROBLEM_NODES
            and catalog
            and p.category.value in catalog
            and self._sure(p, "category", "urgency", "work_type")
        ):
            return Interpretation(
                intent="continue",
                problem=text,
                category=p.category.value,
                urgency=p.urgency.value,
                work_type=p.work_type.value,
                llm_ms=p.ms,
                origin="classifier",
            )
        return None

    def classify(self, request: str, catalog: list[str]) -> Classification | None:
        """Category, urgency and work type of the problem, or None to ask the LLM."""
        if not request.strip():
            return None
        p = self.classifier.predict(request)
        if p.category.value not in catalog or not self._sure(p, "category", "urgency", "work_type"):
            return None
        return Classification(
            category=str(p.category.value),
            urgency=str(p.urgency.value),
            work_type=str(p.work_type.value),
            ms=p.ms,
        )


def _no_entities(text: str) -> dict[str, str]:
    return {}


def build_router(model_dir: str, threshold: float) -> HybridRouter | None:
    """The router for the settings, or None if the classifier or spaCy are not available."""
    from building_call.nlp.classifier import load_classifier

    classifier = load_classifier(model_dir)
    if classifier is None:
        return None
    try:
        from building_call.nlp.entities import extract_entities, pipeline

        pipeline()
        entities: EntityExtractor = extract_entities
    except ImportError:
        from building_call.nlp.entities import regex_entities

        entities = regex_entities
    return HybridRouter(classifier, threshold, entities)
