"""English fake model for the `tests/en` suite: simple rules over English text.

The same role as `FakeLLM` in `tests/conftest.py`, for the English prompts (English JSON keys,
"Caller's answer: ..."). Separate module so the English tests can import it without clashing
with the Spanish `conftest`.
"""

from __future__ import annotations

import re
from typing import Any

from building_call.llm import wire


def _work_type_and_equipment(u: str, system: str) -> dict[str, str]:
    """Vandalism or theft -> insurance_claim; the equipment, if the sentence names its type and
    it is in the list."""
    output: dict[str, str] = {}
    if any(p in u for p in ("vandaliz", "stolen", "smashed")):
        output["work_type"] = "insurance_claim"
    for eq_id, description in re.findall(r"(eq-[\w-]+) \(([^)]*)\)", system):
        kind = description.split()[0]  # "intercom", "elevator", "garage"
        if kind in u:
            output["equipment"] = eq_id
    return output


def _classify(u: str, catalog: list[str]) -> tuple[str, str]:
    if any(p in u for p in ("gas", "smoke", "fire")):
        cat, urg = "unclassifiable", "emergency"
    elif "water" in u or "leak" in u or "faucet" in u:
        cat, urg = "plumbing", "high" if "flood" in u else "medium"
    elif "light" in u or "outlet" in u:
        cat, urg = "electrical", "medium"
    elif "elevator" in u:
        cat, urg = "elevator", "emergency" if "inside" in u else "high"
    elif "cleaning" in u or "trash" in u:
        cat, urg = "cleaning", "low"
    else:
        cat, urg = "unclassifiable", "medium"
    assert cat in catalog
    return cat, urg


def _domain_catalog(schema: dict[str, Any]) -> list[str]:
    """The schema's catalog with domain codes."""
    return [wire.from_model(wire.CATEGORIES, c) for c in schema["properties"]["category"]["enum"]]


_FLOOR_WORDS = r"\d+|first|second|third|fourth|ground"


class FakeLLM:
    """Simulates structured extraction with simple rules. Dispatches on the schema `title`."""

    name = "fake/deterministic"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.system = ""

    async def available(self) -> bool:
        return True

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:
        title = schema.get("title")
        self.calls.append((title or "", user))
        self.system = system
        if title == "interpretation":
            return self._interpret(user, schema)
        if title == "phrasing":
            m = re.search(r'REQUIRED MESSAGE: """(.*)"""', user, re.DOTALL)
            return {"reply": "Okay. " + (m.group(1) if m else "")}
        if title == "classification":
            catalog = _domain_catalog(schema)
            cat, urg = _classify(user.lower(), catalog)
            return {
                "category": cat,
                "urgency": urg,
                "summary": f"The caller reports: {user}",
                "work_type": "repair",
                **_work_type_and_equipment(user.lower(), system),
            }
        raise AssertionError(f"unknown schema: {title}")

    def _interpret(self, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        # The user message has the question and the answer; only the answer is interpreted
        m = re.search(r'Caller\'s answer: """(.*)"""', user, re.DOTALL)
        text = (m.group(1) if m else user).strip()
        u = text.lower()
        output: dict[str, Any] = {}
        if any(p in u for p in ("speak to a person", "talk to someone", "a real person")):
            return {"intent": "human"}
        if any(p in u for p in ("goodbye", "bye", "i'll call back")):
            return {"intent": "hang_up"}
        if any(p in u for p in ("didn't hear you", "say that again", "what?", "didn't understand")):
            return {"intent": "repeat"}
        if "being recorded" in u or "why do you need" in u:
            return {"intent": "question", "question": text}
        if "how much do i pay" in u or "fees" in u:
            return {"intent": "out_of_scope"}
        if u.startswith(("yes", "yeah", "that's right", "correct")):
            output["confirms"] = True
        elif u.startswith("no"):
            output["confirms"] = False
        m_addr = re.search(r"(\w+ main street|main street \w+|\w+ elm avenue|elm avenue \w+)", u)
        if m_addr:
            output["address"] = m_addr.group(1)
        m_entrance = re.search(r"entrance\s*(\w+)", u)
        remainder = re.sub(r"entrance\s*\w+", " ", u)
        floors = re.findall(
            rf"\b({_FLOOR_WORDS})(?:st|nd|rd|th)?\b\s*(?:floor,?\s*)?([a-d])\b", remainder
        )
        m_floor = floors[-1] if floors else None  # a correction says the right one last
        if m_entrance:
            output["entrance"] = m_entrance.group(1)
        if m_floor:
            output["floor"], output["door"] = m_floor[0], m_floor[1].upper()
        m_name = re.search(r"(?:i'm|my name is|this is)\s+([a-z]+(?:\s+(?!and\b)[a-z]+)?)", u)
        if m_name and m_name.group(1).split()[0] not in {"calling", "in", "at", "on", "the"}:
            output["name"] = " ".join(w.capitalize() for w in m_name.group(1).split())
        elif re.fullmatch(r"[a-z]+(?:\s+[a-z]+)?", u) and "confirms" not in output and not m_addr:
            output["name"] = text.title()
        problem_words = (
            "leak",
            "water",
            "light",
            "elevator",
            "trash",
            "ufo",
            "outlet",
            "door of",
            "broken",
            "noise",
            "stopped",
            "intercom",
            "gas",
            "smoke",
            "fire",
        )
        if any(p in u for p in problem_words) and "problem" not in output:
            output["problem"] = text
            if "category" in schema["properties"]:
                cat, urg = _classify(u, _domain_catalog(schema))
                output.update(category=cat, urgency=urg, summary=f"The caller reports: {text}")
                output.update(_work_type_and_equipment(u, self.system))
        if "but an electrician can't fix" in u:
            output["objection"] = text.split("but", 1)[1].strip()
        if "correct that" in u or "i made a mistake" in u or "earlier i said" in u:
            output["intent"] = "correct"
        return output
