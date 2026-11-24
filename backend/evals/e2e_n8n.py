"""End to end against a real n8n: a simulated call sends signed events and calls the book_visit
tool, with the real graph and the configured LLM (Ollama locally).

    docker compose --profile n8n up -d n8n          # workflows imported and active
    cd backend
    sb run -- uv run python evals/e2e_n8n.py --verbose

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

CALLER = [
    "Vivo en la calle Mayor número 12",
    "sí",
    "tercero B",
    "Hay una fuga de agua en el baño y está inundando el suelo",
]
FALLBACK_ANSWER = "sí, correcto"


class Recording:
    """Wraps the real fan-out to also keep what was emitted and how each delivery ended."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.tasks: list[tuple[str, asyncio.Task[Any]]] = []

    def emit(self, event: dict[str, Any]) -> None:
        for task in self.inner.emit(event):
            self.tasks.append((event["type"], task))


async def run(verbose: bool) -> dict[str, Any]:
    fan = create_webhook_sender(settings)
    tools = create_n8n_tools(settings)
    if fan is None or tools is None:
        sys.exit("Set BC_N8N_WEBHOOK_URL, BC_N8N_TOOL_URL and BC_N8N_WEBHOOK_SECRET")
    sink = Recording(fan)
    manager = CallManager(Dependencies(MemoryRepository(), create_llm(), tools=tools), events=sink)
    state = await manager.begin("+34600000000")
    turns = [*CALLER]
    timings: list[dict[str, Any]] = []
    while not state.get("finished") and len(timings) < 12:
        text = turns.pop(0) if turns else FALLBACK_ANSWER
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
    result = asyncio.run(run(p.parse_args().verbose))
    out = Path(__file__).with_name("last_e2e_n8n.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
