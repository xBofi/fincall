"""End to end against a real n8n: a simulated call sends signed events and calls the book_visit
tool, with the real graph and the configured LLM (Ollama locally).

    docker compose --profile n8n up -d n8n          # workflows imported and active
    cd backend
    sb run -- uv run python evals/e2e_n8n.py --verbose [--language en]

Reads BC_N8N_WEBHOOK_URL, BC_N8N_TOOL_URL and BC_N8N_WEBHOOK_SECRET from the environment (the
secret through SecretBroker, never printed). Writes the result to evals/last_e2e_n8n.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from building_call.api.main import create_llm, create_n8n_tools, create_webhook_sender
from building_call.graph.nodes import Dependencies
from building_call.repos.memory import MemoryRepository
from building_call.services import CallManager
from building_call.settings import settings

#: What the simulated caller says, per language (demo buildings of `repos.demo_data`). Adding a
#: language = one entry here with its demo building's address.
CALLERS: dict[str, tuple[list[str], str]] = {
    "es": (
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
        ],
        "sí, correcto",
    ),
    "en": (
        [
            "I live at number 12 Main Street",
            "yes",
            "third floor, B",
            "There's a water leak in the bathroom and it's flooding the floor",
        ],
        "yes, correct",
    ),
    # Catalan uses the Spanish demo buildings; Arabic, Hindi and Urdu the US ones
    "ca": (
        ["Visc al carrer Major 12", "sí", "tercer B", "Hi ha una fuita d'aigua al bany"],
        "sí, correcte",
    ),
    "ar": (
        ["أسكن في 12 Main Street", "نعم", "شقة 3B", "يوجد تسرب ماء في الحمام"],
        "نعم، صحيح",
    ),
    "hi": (
        ["मैं 12 Main Street में रहता हूँ", "हाँ", "फ्लैट 3B", "बाथरूम में पानी का रिसाव है"],
        "हाँ, सही है",
    ),
    "ur": (
        ["میں 12 Main Street میں رہتا ہوں", "جی ہاں", "فلیٹ 3B", "باتھ روم میں پانی لیک ہو رہا ہے"],
        "جی ہاں، درست ہے",
    ),
}


class Recording:
    """Wraps the real fan-out to also keep what was emitted and how each delivery ended."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.tasks: list[tuple[str, asyncio.Task[Any]]] = []

    def emit(self, event: dict[str, Any]) -> None:
        for task in self.inner.emit(event):
            self.tasks.append((event["type"], task))


async def run(verbose: bool, language: str) -> dict[str, Any]:
    fan = create_webhook_sender(settings)
    tools = create_n8n_tools(settings)
    if fan is None or tools is None:
        sys.exit("Set BC_N8N_WEBHOOK_URL, BC_N8N_TOOL_URL and BC_N8N_WEBHOOK_SECRET")
    sink = Recording(fan)
    repo = MemoryRepository(language)
    manager = CallManager(Dependencies(repo, create_llm(), tools=tools), events=sink)
    state = await manager.begin("+34600000000", language)
    script, fallback_answer = CALLERS[language]
    turns = [*script]
    timings: list[dict[str, Any]] = []
    while not state.get("finished") and len(timings) < 12:
        text = turns.pop(0) if turns else fallback_answer
        t0 = perf_counter()
        state = await manager.message(state["call_id"], text)
        timings.append(
            {
                "caller": text,
                "ms": int((perf_counter() - t0) * 1000),
                "node": state.get("current_node"),
            }
        )
        if verbose:
            print(f"vecino: {text}\nbot:    {' '.join(state.get('bot_messages', []))}\n")
    await fan.drain(15)
    deliveries = [
        {"type": t, **{k: v for k, v in vars(task.result()).items() if k != "error" or v}}
        for t, task in sink.tasks
        if task.done()
    ]
    result = {
        "language": language,
        "finished": state.get("finished"),
        "result": state.get("result"),
        "visit": state.get("visit"),
        "closing": state.get("bot_messages", [])[-1:],
        "turns": timings,
        "deliveries": deliveries,
    }
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--language", choices=sorted(CALLERS), default="es")
    args = p.parse_args()
    result = asyncio.run(run(args.verbose, args.language))
    suffix = "" if args.language == "es" else f"_{args.language}"
    out = Path(__file__).with_name(f"last_e2e_n8n{suffix}.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
