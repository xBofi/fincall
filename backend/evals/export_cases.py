"""Exporta al banco de pruebas los turnos que calidad ha marcado como caso desde la consola.

Cada caso va a `gold_interpretation.jsonl` (lo que el bot debería haber entendido del turno) o a
`gold_classification.jsonl` (categoría y urgencia de la solicitud), con el mismo formato que los
casos escritos a mano y que lee `compare_models.py` ("catalogo", "equipos", "oido" incluidos).

Por privacidad solo sale el texto del turno y la pregunta del bot: nunca la llamada, el
teléfono, el nombre del vecino (ya seudonimizado al marcar), el autor ni la nota del revisor.
No duplica casos ya presentes en el banco (misma frase, sin distinguir mayúsculas ni espacios).

Uso:
    # Desde la base de datos configurada (BC_REPOSITORY=postgres en backend/.env)
    uv run python evals/export_cases.py --simular
    uv run python evals/export_cases.py
    # Desde el JSON de GET /api/operations/cases descargado de la consola
    uv run python evals/export_cases.py --entrada casos.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from building_call.bank_cases import strip_phones  # noqa: E402

HERE = Path(__file__).resolve().parent
INTERPRETATION = "gold_interpretation.jsonl"
CLASSIFICATION = "gold_classification.jsonl"


def _text(value: Any) -> Any:
    """Segunda barrera: aunque el caso ya se guardó limpio, ningún teléfono sale al banco."""
    if isinstance(value, str):
        return strip_phones(value)
    if isinstance(value, list):
        return [_text(v) for v in value]
    return value


def case_to_line(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(fichero del banco, línea) de un caso marcado. Solo los campos del banco, en su orden."""
    utterance = _text(case["utterance"])
    expected = {k: _text(v) for k, v in case["expected"].items()}
    if case.get("bank") == "classification":
        urgencies = expected.get("urgency")
        return CLASSIFICATION, {
            "request": utterance,
            "category": expected["category"],
            "urgencies": urgencies if isinstance(urgencies, list) else [urgencies],
        }
    line: dict[str, Any] = {"node": case["node"], "question": _text(case.get("question"))}
    if case.get("heard_text"):
        line["heard_text"] = _text(case["heard_text"])
    line["utterance"] = utterance
    if case.get("catalog"):
        line["catalog"] = True
    if case.get("equipment_list"):
        line["equipment_list"] = [[str(i), _text(str(d))] for i, d in case["equipment_list"]]
    line["expected"] = expected
    return INTERPRETATION, line


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def key(file: str, line: dict[str, Any]) -> tuple[str, ...]:
    """Lo que hace que dos casos sean el mismo: la misma frase en la misma situación."""
    if file == CLASSIFICATION:
        return (file, _norm(line["request"]))
    return (
        file,
        _norm(line.get("node")),
        _norm(line.get("question")),
        _norm(line.get("heard_text")),
        _norm(line["utterance"]),
    )


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def export(
    cases: list[dict[str, Any]], target: Path = HERE, *, simulate: bool = False
) -> dict[str, int]:
    """Añade al banco los casos que no estén ya. Devuelve cuántos añade a cada fichero y cuántos
    omite por repetidos."""
    seen = {
        key(file, line)
        for file in (INTERPRETATION, CLASSIFICATION)
        for line in _read(target / file)
    }
    new_entries: dict[str, list[dict[str, Any]]] = {INTERPRETATION: [], CLASSIFICATION: []}
    omitted = 0
    for case in cases:
        file, line = case_to_line(case)
        k = key(file, line)
        if k in seen:
            omitted += 1
            continue
        seen.add(k)
        new_entries[file].append(line)
    if not simulate:
        for file, lines in new_entries.items():
            if not lines:
                continue
            path = target / file
            previous = path.read_text(encoding="utf-8") if path.exists() else ""
            separator = "" if not previous or previous.endswith("\n") else "\n"
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(separator)
                for line in lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return {INTERPRETATION: len(new_entries[INTERPRETATION]), CLASSIFICATION: len(new_entries[CLASSIFICATION]),
            "omitted": omitted}  # fmt: skip


async def _cases_from_db() -> list[dict[str, Any]]:
    from building_call.settings import settings

    if settings.repository != "postgres":
        raise SystemExit(
            "Sin base de datos (BC_REPOSITORY no es postgres): descarga los casos de "
            "GET /api/operations/cases y pásalos con --entrada"
        )
    from building_call.repos.postgres import PostgresRepository

    repo = PostgresRepository(settings.database_url)
    try:
        return await repo.list_test_bank_cases()
    finally:
        await repo.engine.dispose()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--utterance", type=Path, help="JSON descargado de GET /api/operations/cases")
    ap.add_argument("--target", type=Path, default=HERE, help="Carpeta con los gold_*.jsonl")
    ap.add_argument("--simulate", action="store_true", help="Cuenta lo que añadiría sin escribir")
    args = ap.parse_args(argv)
    if args.utterance:
        cases = json.loads(args.utterance.read_text(encoding="utf-8"))
    else:
        cases = asyncio.run(_cases_from_db())
    r = export(cases, args.target, simulate=args.simulate)
    verb = "Añadiría" if args.simulate else "Añadidos"
    print(
        f"{verb}: {r[INTERPRETATION]} a {INTERPRETATION}, {r[CLASSIFICATION]} a {CLASSIFICATION}; "
        f"{r['omitted']} ya estaban en el banco"
    )


if __name__ == "__main__":
    main()
