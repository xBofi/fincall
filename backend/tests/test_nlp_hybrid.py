"""Turn classifier wrapper, hybrid router and its hook in the graph (with fakes: no torch)."""

from __future__ import annotations

from typing import Any

import pytest
from es_helpers import FakeLLM

from building_call.graph.nodes import Dependencies
from building_call.nlp.classifier import (
    Label,
    SetFitTurnClassifier,
    TurnPrediction,
    load_classifier,
)
from building_call.nlp.hybrid import HybridRouter
from building_call.nlp.scoring import per_class_f1, reference, score
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager

CATALOG = ["plumbing", "electrical", "elevator", "unclassifiable"]


class FakeEncoder:
    """Embedding = which keyword the text has (one-hot), like a tiny sentence-transformer."""

    KEYWORDS = ("persona", "agua", "luz", "?")

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, sentences: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls += 1
        return [[1.0 if k in s.lower() else 0.0 for k in self.KEYWORDS] for s in sentences]


class FakeHead:
    """predict_proba: the class of the first active keyword gets `sure`, the rest share."""

    def __init__(self, classes: list[str], by_keyword: dict[int, str], sure: float = 0.95):
        self.classes_ = classes
        self.by_keyword = by_keyword
        self.sure = sure

    def predict_proba(self, x: list[list[float]]) -> list[list[float]]:
        out = []
        for row in x:
            hit = next(
                (self.by_keyword[i] for i, v in enumerate(row) if v and i in self.by_keyword), None
            )
            target = hit or self.classes_[0]
            rest = (1 - self.sure) / (len(self.classes_) - 1)
            out.append([self.sure if c == target else rest for c in self.classes_])
        return out


def fake_classifier(sure: float = 0.95) -> SetFitTurnClassifier:
    heads = {
        "intent": FakeHead(["continue", "human", "question"], {0: "human", 3: "question"}, sure),
        "category": FakeHead(["electrical", "plumbing"], {1: "plumbing", 2: "electrical"}, sure),
        "urgency": FakeHead(["medium", "high"], {1: "high"}, sure),
        "work_type": FakeHead(["repair", "replacement"], {}, sure),
    }
    return SetFitTurnClassifier(FakeEncoder(), heads, name="fake")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- classifier wrapper


def test_one_encode_answers_every_task() -> None:
    clf = fake_classifier()
    p = clf.predict("Hay una fuga de agua")
    assert p.intent == Label("continue", 0.95)
    assert p.category.value == "plumbing" and p.urgency.value == "high"
    assert p.work_type.value == "repair"
    assert clf.encoder.calls == 1  # type: ignore[attr-defined]


def test_predict_many_keeps_order() -> None:
    preds = fake_classifier().predict_many(["no hay luz", "quiero una persona"])
    assert [p.category.value for p in preds] == ["electrical", "electrical"]
    assert [p.intent.value for p in preds] == ["continue", "human"]
    assert fake_classifier().predict_many([]) == []


def test_without_weights_the_classifier_is_not_loaded(tmp_path: Any) -> None:
    assert load_classifier(tmp_path / "missing") is None


# --------------------------------------------------------------------------- router


def test_global_intent_without_data_skips_the_llm() -> None:
    router = HybridRouter(fake_classifier(), threshold=0.9)
    interp = router.interpret("Quiero hablar con una persona", "identify_estate", CATALOG)
    assert interp is not None and interp.intent == "human" and interp.origin == "classifier"


def test_question_keeps_the_text_to_answer_it() -> None:
    interp = HybridRouter(fake_classifier()).interpret("¿Esto se graba?", "collect_request")
    assert interp is not None and interp.intent == "question"
    assert interp.question == "¿Esto se graba?"


def test_below_threshold_goes_to_the_llm() -> None:
    router = HybridRouter(fake_classifier(sure=0.6), threshold=0.85)
    assert router.interpret("Quiero hablar con una persona", "identify_estate") is None


def test_entities_in_the_turn_go_to_the_llm() -> None:
    router = HybridRouter(fake_classifier(), entities=lambda t: {"floor": "3"})
    assert router.interpret("Una persona, del tercero B", "identify_home") is None


def test_problem_only_in_collect_request_and_with_a_catalog() -> None:
    router = HybridRouter(fake_classifier())
    interp = router.interpret("Hay una fuga de agua", "collect_request", CATALOG)
    assert interp is not None
    assert (interp.problem, interp.category, interp.urgency, interp.work_type) == (
        "Hay una fuga de agua", "plumbing", "high", "repair",
    )  # fmt: skip
    assert router.interpret("Hay una fuga de agua", "identify_home", CATALOG) is None
    # Without a catalog (the estate has equipment to choose) the LLM decides
    assert router.interpret("Hay una fuga de agua", "collect_request", None) is None


def test_category_outside_the_catalog_goes_to_the_llm() -> None:
    router = HybridRouter(fake_classifier())
    assert router.interpret("Hay una fuga de agua", "collect_request", ["elevator"]) is None
    assert router.classify("Hay una fuga de agua", ["elevator"]) is None


def test_correct_is_never_decided_by_the_classifier() -> None:
    class Always:
        name = "always"

        def predict(self, text: str) -> TurnPrediction:
            return TurnPrediction(intent=Label("correct", 0.99))

        def predict_many(self, texts: list[str]) -> list[TurnPrediction]:
            return [self.predict(t) for t in texts]

    assert HybridRouter(Always()).interpret("me he equivocado", "identify_home") is None


def test_classify_problem_shape() -> None:
    c = HybridRouter(fake_classifier()).classify("no hay luz", CATALOG)
    assert c is not None and (c.category, c.urgency, c.work_type) == (
        "electrical", "medium", "repair",
    )  # fmt: skip


# --------------------------------------------------------------------------- graph hook


@pytest.fixture
def hybrid_manager(llm: FakeLLM) -> CallManager:
    repo = MemoryRepository()
    repo.administrator_config["adm-demo"] = repo.administrator_config["adm-demo"].model_copy(
        update={"ask_detail": False}
    )
    return CallManager(
        Dependencies(repo=repo, llm=llm, router=HybridRouter(fake_classifier(), 0.9))
    )


async def test_graph_hands_over_without_calling_the_llm(
    hybrid_manager: CallManager, llm: FakeLLM
) -> None:
    state = await hybrid_manager.begin("+34600")
    state = await hybrid_manager.message(state["call_id"], "Quiero hablar con una persona")
    assert state["result"] == "handed_to_administrator"
    assert not [c for c in llm.calls if c[0] == "interpretation"]
    assert state["turn_timings"]["interpretation_origin"] == "classifier"


async def test_graph_safety_rule_still_applies(hybrid_manager: CallManager) -> None:
    """The classifier says `human`, but without explicit words the call is not handed over."""

    class SaysHuman:
        name = "human"

        def predict(self, text: str) -> TurnPrediction:
            return TurnPrediction(intent=Label("human", 0.99))

        def predict_many(self, texts: list[str]) -> list[TurnPrediction]:
            return [self.predict(t) for t in texts]

    hybrid_manager.deps.router = HybridRouter(SaysHuman())
    state = await hybrid_manager.begin("+34600")
    state = await hybrid_manager.message(state["call_id"], "Mmm vale ya")
    assert state.get("result") != "handed_to_administrator"


# --------------------------------------------------------------------------- scoring


def test_scoring_accepts_any_listed_answer() -> None:
    assert reference(["low", "medium"], "medium") == "medium"
    assert reference(["low", "medium"], "high") == "low"
    assert reference([None, "continue"], None) == "continue"
    s = score([["low", "medium"], "high", "high"], ["medium", "high", "low"])
    assert s["accuracy"] == pytest.approx(2 / 3)
    assert set(s["per_class"]) == {"medium", "high"}


def test_per_class_f1() -> None:
    f1 = per_class_f1(["a", "a", "b"], ["a", "b", "b"])
    assert f1["a"] == pytest.approx(2 / 3) and f1["b"] == pytest.approx(2 / 3)


def test_the_trained_weights_load_and_answer() -> None:
    """Smoke test of the real model; skipped without the nlp extra or the trained weights."""
    from pathlib import Path

    pytest.importorskip("sentence_transformers")
    weights = Path(__file__).resolve().parents[1] / "nlp" / "models" / "turn-classifier"
    if not (weights / "heads.joblib").exists():
        pytest.skip("no trained weights: run nlp/train_classifier.py")
    clf = load_classifier(weights)
    assert clf is not None
    p = clf.predict("Quiero hablar con una persona")
    assert p.intent.value == "human" and p.intent.confidence > 0.5
    assert clf.predict("Hay una fuga de agua en el garaje").category.value == "plumbing"
