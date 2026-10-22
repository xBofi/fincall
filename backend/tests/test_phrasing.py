from __future__ import annotations

from typing import Any

from building_call.graph.nodes import Dependencies
from building_call.llm.phrasing import keeps_facts, phrase, required_facts
from building_call.services import CallManager


def test_required_facts_extracts_numbers_references_and_names() -> None:
    template = (
        "He identificado Avenida de la Alameda 5. ¿Es correcto? Perfecto, 3º B. "
        "He registrado su incidencia con referencia inc-abc123. Se avisará a Fontanería Pérez."
    )
    facts = required_facts(template)
    assert "5" in facts and "3º" in facts and "inc-abc123" in facts
    assert "Avenida" in " ".join(facts) and "Fontanería Pérez" in facts


def test_keeps_facts_detects_omissions() -> None:
    template = "He identificado Calle Mayor 12. ¿Es correcto?"
    assert keeps_facts(template, "Entendido, ¿vive usted en Calle Mayor 12?")
    assert not keeps_facts(template, "Entendido, ¿vive usted en Calle Mayor?")
    assert not keeps_facts(template, "Vive en Calle Mayor 12.")  # perdió la pregunta


class FakePhraser:
    name = "phraser"

    def __init__(self, reply: str | None, tarda: float = 0) -> None:
        self.reply = reply
        self.tarda = tarda

    async def extract(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        format_instruction: str | None = None,
    ) -> dict[str, Any]:  # noqa: E501
        import asyncio

        await asyncio.sleep(self.tarda)
        if self.reply is None:
            raise RuntimeError("caído")
        return {"reply": self.reply}


async def test_phrase_uses_template_if_it_loses_facts_or_fails() -> None:
    template = "He identificado Calle Mayor 12. ¿Es correcto?"
    text, m = await phrase(FakePhraser("¿Vive en Calle Mayor?"), template, "en mayor", {})
    assert text == template and m["phrasing"].startswith("template")
    text, m = await phrase(FakePhraser(None), template, "en mayor", {})
    assert text == template and m["phrasing"].startswith("failure")
    text, m = await phrase(
        FakePhraser("x", tarda=0.3), template, "en mayor", {}, timeout_s=0.05
    )
    assert text == template
    text, m = await phrase(
        FakePhraser("Entendido. ¿Vive usted en Calle Mayor 12?"), template, "en mayor", {}
    )
    assert text == "Entendido. ¿Vive usted en Calle Mayor 12?" and m["phrasing"] == "llm"


async def test_manager_phrases_only_llm_interpreted_turns(deps: Dependencies) -> None:
    manager = CallManager(deps, phraser=deps.llm)  # el LLM falso antepone "Vale. "
    state = await manager.begin()
    assert state["bot_messages"][0].startswith("Hola")  # saludo: plantilla tal cual
    state = await manager.message(state["call_id"], "Calle Mayor 12")
    assert state["bot_messages"] == [
        "He identificado Calle Mayor 12. ¿Es correcto?"
    ]  # determinista
    state = await manager.message(state["call_id"], "sí")
    state = await manager.message(
        state["call_id"], "Pues mire, vivo en el tercero B y me llamo Marta, y hay una fuga"
    )
    # Turno interpretado por el LLM: se redacta y el historial guarda lo dicho
    assert state["bot_messages"][0].startswith("Vale. ")
    assert state["history"][-1]["text"] == state["bot_messages"][0]
    assert state["history"][-2]["role"] == "caller"
    assert state["turn_timings"]["phrasing"] == "llm"


def test_no_local_phrasing_with_api_provider(monkeypatch: Any) -> None:
    # Con Vertex o Workers AI no hay Ollama: cada turno esperaba en balde 750 ms
    from building_call.api.main import create_phraser
    from building_call.settings import settings

    monkeypatch.setattr(settings, "phrasing", "llm")
    for provider in ("vertex", "workers_ai"):
        monkeypatch.setattr(settings, "llm", provider)
        assert create_phraser() is None
    monkeypatch.setattr(settings, "llm", "ollama")
    assert create_phraser() is not None
