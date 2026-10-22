"""Exportador de los casos marcados por calidad al banco de pruebas (`evals/gold_*.jsonl`)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

EVALS = Path(__file__).resolve().parents[1] / "evals"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_cases", EVALS / "export_cases.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_cases"] = mod
    spec.loader.exec_module(mod)
    return mod


export_cases = _module()


def _case(**changes: object) -> dict:
    base = {
        "id": "cb-1",
        "call_id": "llam-abc",
        "turn": 3,
        "bank": "interpretation",
        "node": "collect_request",
        "question": "Cuénteme, ¿qué ocurre?",
        "heard_text": None,
        "utterance": "El portero no se oye, llamadme al 600 11 22 33",
        "catalog": True,
        "equipment_list": [["eq-1", "portero automático Fermax"]],
        "expected": {"problem": "*", "equipment": "eq-1"},
        "note": "Lo dijo Remedios, muy enfadada",
        "author_id": "usr-quality",
        "created_at": "2026-10-22T10:00:00+00:00",
        "caller_phone": "+34600111222",
        "declared_name": "Remedios",
    }
    return {**base, **changes}


def _write(path: Path, lines: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8"
    )


def test_interpretation_format_same_as_the_test_bank() -> None:
    file, line = export_cases.case_to_line(_case())
    assert file == "gold_interpretation.jsonl"
    # Mismo orden de claves que los casos escritos a mano; solo turno y pregunta del bot
    assert list(line) == ["node", "question", "utterance", "catalog", "equipment_list", "expected"]
    assert line["utterance"] == "El portero no se oye, llamadme al 600 000 000"
    assert line["equipment_list"] == [["eq-1", "portero automático Fermax"]]
    text = json.dumps(line, ensure_ascii=False)
    for private_value in ("600111222", "Remedios", "llam-abc", "usr-quality", "enfadada"):
        assert private_value not in text


def test_heard_and_without_catalog_or_equipment() -> None:
    _, line = export_cases.case_to_line(
        _case(heard_text="Le resumo: gotea", catalog=False, equipment_list=None, expected={"confirms": True})
    )
    assert list(line) == ["node", "question", "heard_text", "utterance", "expected"]


def test_classification_format() -> None:
    file, line = export_cases.case_to_line(
        _case(
            bank="classification",
            utterance="Hay una fuga en el garaje",
            expected={"category": "plumbing", "urgency": "high"},
        )
    )
    assert file == "gold_classification.jsonl"
    assert line == {
        "request": "Hay una fuga en el garaje",
        "category": "plumbing",
        "urgencies": ["high"],
    }


def test_does_not_duplicate_existing_cases(tmp_path: Path) -> None:
    _write(
        tmp_path / "gold_interpretation.jsonl",
        [
            {
                "node": "collect_request",
                "question": "Cuénteme, ¿qué ocurre?",
                "utterance": "el portero no se oye,  llamadme al 600 000 000",
                "expected": {"problem": "*"},
            }
        ],
    )
    _write(
        tmp_path / "gold_classification.jsonl",
        [{"request": "Gotea el grifo", "category": "plumbing", "urgencies": ["low"]}],
    )
    cases = [
        _case(),  # ya está (misma frase, distinta capitalización y espacios)
        _case(id="cb-2", utterance="Se ha ido la luz del portal"),
        _case(id="cb-3", utterance="Se ha ido la luz del portal"),  # repetido en la tanda
        _case(
            id="cb-4",
            bank="classification",
            utterance="gotea el grifo",
            expected={"category": "plumbing", "urgency": "low"},
        ),
        _case(
            id="cb-5",
            bank="classification",
            utterance="El ascensor hace ruido",
            expected={"category": "elevator", "urgency": ["low", "medium"]},
        ),
    ]
    result = export_cases.export(cases, tmp_path)
    assert result == {
        "gold_interpretation.jsonl": 1,
        "gold_classification.jsonl": 1,
        "omitted": 3,
    }
    interp = (tmp_path / "gold_interpretation.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(interp) == 2 and json.loads(interp[1])["utterance"] == "Se ha ido la luz del portal"
    classif = (tmp_path / "gold_classification.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(classif[-1]) == {
        "request": "El ascensor hace ruido",
        "category": "elevator",
        "urgencies": ["low", "medium"],
    }
    # Idempotente: una segunda pasada no añade nada
    assert export_cases.export(cases, tmp_path)["omitted"] == 5


def test_simulation_does_not_write(tmp_path: Path) -> None:
    (tmp_path / "gold_interpretation.jsonl").write_text("", encoding="utf-8")
    r = export_cases.export([_case()], tmp_path, simulate=True)
    assert r["gold_interpretation.jsonl"] == 1
    assert (tmp_path / "gold_interpretation.jsonl").read_text(encoding="utf-8") == ""


def test_main_reads_a_json_downloaded_from_the_console(tmp_path: Path) -> None:
    download = tmp_path / "cases.json"
    download.write_text(json.dumps([_case()]), encoding="utf-8")
    export_cases.main(["--utterance", str(download), "--target", str(tmp_path)])
    lines = (tmp_path / "gold_interpretation.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["node"] == "collect_request"


def test_exported_cases_are_understood_by_compare_models(tmp_path: Path) -> None:
    """Las claves que usa `evals/compare_models.py` para reproducir el turno."""
    _, line = export_cases.case_to_line(_case(heard_text="Cuén"))
    for key in ("node", "question", "utterance", "expected", "catalog", "equipment_list", "heard_text"):
        assert key in line
    assert all(isinstance(e, list) and len(e) == 2 for e in line["equipment_list"])
