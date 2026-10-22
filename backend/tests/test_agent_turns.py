"""Configuración de turnos del agente de LiveKit: STT y corte del bot por settings."""

from __future__ import annotations

import pytest

from building_call.settings import settings

agent = pytest.importorskip("building_call.voice.agent", reason="requiere el extra voz")


def test_the_stt_uses_the_configured_model_and_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_model", "universal-streaming-multilingual")
    monkeypatch.setattr(settings, "assemblyai_eot_confidence", 0.6)
    monkeypatch.setattr(settings, "livekit_stt_silence_min_ms", 300)
    monkeypatch.setattr(settings, "livekit_stt_silence_max_ms", 1000)
    o = agent.stt_options(["Mayor", "Alameda"])
    assert o["model"] == "universal-streaming-multilingual"
    assert o["end_of_turn_confidence_threshold"] == 0.6
    assert o["min_turn_silence"] == 300 and o["max_turn_silence"] == 1000
    assert o["keyterms_prompt"] == ["Mayor", "Alameda"]
    assert o["base_url"].startswith("wss://") and o["base_url"].endswith(".eu.assemblyai.com")
    assert "language_codes" not in o  # el multilingüe no acepta fijar idioma


def test_by_default_does_not_close_the_turn_on_a_comma_pause() -> None:
    """Con 220 ms AssemblyAI partía "Sí, es correcto" en dos turnos (6 de 32 frases del banco);
    desde 300 ms, 1 de 32 (medido 2026-10-22)."""
    o = agent.stt_options([])
    assert o["min_turn_silence"] >= 300


def test_universal_3_sets_spanish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_model", "universal-3-6-pro")
    assert agent.stt_options([])["language_codes"] == "es"


def test_the_stt_rejects_a_host_outside_the_eu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_streaming_host", "streaming.assemblyai.com")
    with pytest.raises(Exception):  # noqa: B017 - el tipo es el de validar_endpoint_eu
        agent.stt_options([])


def test_turns_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "livekit_end_of_turn_min_s", 0.2)
    monkeypatch.setattr(settings, "livekit_interruption_min_s", 0.8)
    monkeypatch.setattr(settings, "livekit_interruption_min_words", 2)
    t = agent.turn_options()
    assert t["turn_detection"] == "stt"
    assert t["endpointing"]["min_delay"] == 0.2
    assert t["interruption"]["mode"] == "vad"
    assert t["interruption"]["min_duration"] == 0.8
    assert t["interruption"]["min_words"] == 2
    # El grafo tiene efectos: nada de turnos especulativos de LiveKit
    assert t["preemptive_generation"]["enabled"] is False


def test_by_default_does_not_pass_the_bot_speech_to_the_stt() -> None:
    """Con la última pregunta del bot como contexto, Universal-3 Pro cierra antes el turno tras
    un "Sí." o un "No." (3 de 32 frases partidas frente a 1 de 32, medido 2026-10-22)."""
    o = agent.stt_context_options()
    assert o["forward_chat_context"] is False
    assert o["keyterm_detection"] == {"enabled": False}  # usaría un LLM de la nube de LiveKit


def test_the_vad_ends_the_voice_before_assemblyai_ends_the_turn() -> None:
    """Para saber si el vecino sigue hablando cuando AssemblyAI cierra el turno, el VAD tiene que
    dar la voz por acabada antes que el silencio mínimo de AssemblyAI."""
    vad_silence_ms = agent.vad_options()["min_silence_duration"] * 1000
    # Con margen: el VAD también tarda en decidir, y si aún oye el final de la frase al cerrarse
    # el turno se retiene un turno completo
    assert vad_silence_ms <= settings.livekit_stt_silence_min_ms / 2


def test_the_stt_context_can_be_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "livekit_stt_context", True)
    assert agent.stt_context_options()["forward_chat_context"] is True
