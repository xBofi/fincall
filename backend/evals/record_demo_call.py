"""Graba una llamada guionizada a través del grafo real para el modo demo de la vista de enrutado.

Ejecuta el `CallManager` con el repositorio en memoria y el LLM configurado (por defecto Ollama
local), sin telefonía ni voz, y guarda por turno lo que dijo cada parte, los tiempos y las
visitas por nodo (`graph/tracing.py`), junto con la estructura exportada del grafo. El frontend
reproduce ese fichero en el modo demo; `tests/test_routing_view.py` comprueba que la estructura
grabada sigue siendo la del grafo compilado, así que la demo no puede quedarse desfasada.

Uso:
    cd backend
    uv run python evals/record_demo_call.py --model qwen3.5:latest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from building_call.graph.export import export_graph  # noqa: E402
from building_call.graph.nodes import Dependencies  # noqa: E402
from building_call.llm.ollama import OllamaModel  # noqa: E402
from building_call.repos.memory import MemoryRepository  # noqa: E402
from building_call.services import CallManager  # noqa: E402
from building_call.settings import settings  # noqa: E402

OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/components/routing/demo/demoCall.json"
)

#: El vecino del guion: dirección dicha con otras palabras, una pregunta a mitad de llamada
#: (vuelve al mismo nodo sin perder el hilo) y una avería que clasifica el modelo.
SCRIPT = [
    "Hola, buenas, llamo de la calle Mayor doce",
    "Sí, esa es",
    "Tercero B",
    "Oiga, ¿esta llamada se graba?",
    "Vale. Pues hay una fuga de agua en el baño y está inundando el suelo",
    "Sale con bastante fuerza, de debajo del lavabo",
    "Sí, correcto",
]


async def record(model: str) -> dict[str, Any]:
    llm = OllamaModel(
        settings.ollama_url,
        model,
        settings.ollama_timeout_s,
        num_predict=settings.ollama_num_predict,
        num_ctx=settings.ollama_num_ctx,
    )
    if not await llm.available():
        raise SystemExit(f"Ollama no responde o no tiene {model}")
    await llm.warm_up()
    repo = MemoryRepository()
    manager = CallManager(Dependencies(repo=repo, llm=llm))
    turns: list[dict[str, Any]] = []

    def keep(state: dict[str, Any], caller: str | None) -> None:
        turns.append(
            {
                "turn": state["turn"],
                "caller": caller,
                "assistant": state.get("bot_messages", []),
                "initial_node": state["turn_visits"][0]["node"] if state["turn_visits"] else None,
                "final_node": state.get("current_node"),
                "interpretation": state.get("interpretation"),
                "timings": state.get("turn_timings", {}),
                "visits": state.get("turn_visits", []),
            }
        )

    state: dict[str, Any] = dict(await manager.begin())
    keep(state, None)
    for line in SCRIPT:
        state = dict(await manager.message(state["call_id"], line))
        keep(state, line)
        if state.get("finished"):
            break
    return {
        "meta": {
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "llm": llm.name,
            "result": state.get("result"),
            "note": "Llamada guionizada por el grafo real con repositorio en memoria.",
        },
        "structure": export_graph(),
        "turns": turns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=settings.ollama_model)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    data = asyncio.run(record(args.model))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data['turns'])} turnos, resultado {data['meta']['result']} -> {args.output}")


if __name__ == "__main__":
    main()
