"""Configuración de turnos del agente de LiveKit: STT y corte del bot por settings."""

from __future__ import annotations

import pytest

from building_call.settings import settings

agente = pytest.importorskip("building_call.voz.agente", reason="requiere el extra voz")


def test_el_stt_usa_el_modelo_y_los_umbrales_configurados(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_modelo", "universal-streaming-multilingual")
    monkeypatch.setattr(settings, "assemblyai_eot_confianza", 0.6)
    monkeypatch.setattr(settings, "livekit_stt_silencio_min_ms", 300)
    monkeypatch.setattr(settings, "livekit_stt_silencio_max_ms", 1000)
    o = agente.opciones_stt(["Mayor", "Alameda"])
    assert o["model"] == "universal-streaming-multilingual"
    assert o["end_of_turn_confidence_threshold"] == 0.6
    assert o["min_turn_silence"] == 300 and o["max_turn_silence"] == 1000
    assert o["keyterms_prompt"] == ["Mayor", "Alameda"]
    assert o["base_url"].startswith("wss://") and o["base_url"].endswith(".eu.assemblyai.com")
    assert "language_codes" not in o  # el multilingüe no acepta fijar idioma


def test_por_defecto_no_cierra_el_turno_en_la_pausa_de_una_coma() -> None:
    """Con 220 ms AssemblyAI partía "Sí, es correcto" en dos turnos (6 de 32 frases del banco);
    desde 300 ms, 1 de 32 (medido 2026-10-22)."""
    o = agente.opciones_stt([])
    assert o["min_turn_silence"] >= 300


def test_universal_3_fija_el_espanol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_modelo", "universal-3-6-pro")
    assert agente.opciones_stt([])["language_codes"] == "es"


def test_el_stt_rechaza_un_host_fuera_de_la_ue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "assemblyai_streaming_host", "streaming.assemblyai.com")
    with pytest.raises(Exception):  # noqa: B017 - el tipo es el de validar_endpoint_eu
        agente.opciones_stt([])


def test_los_turnos_salen_de_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "livekit_fin_turno_min_s", 0.2)
    monkeypatch.setattr(settings, "livekit_interrupcion_min_s", 0.8)
    monkeypatch.setattr(settings, "livekit_interrupcion_min_palabras", 2)
    t = agente.opciones_turnos()
    assert t["turn_detection"] == "stt"
    assert t["endpointing"]["min_delay"] == 0.2
    assert t["interruption"]["mode"] == "vad"
    assert t["interruption"]["min_duration"] == 0.8
    assert t["interruption"]["min_words"] == 2
    # El grafo tiene efectos: nada de turnos especulativos de LiveKit
    assert t["preemptive_generation"]["enabled"] is False


def test_por_defecto_no_pasa_al_stt_lo_que_dijo_el_bot() -> None:
    """Con la última pregunta del bot como contexto, Universal-3 Pro cierra antes el turno tras
    un "Sí." o un "No." (3 de 32 frases partidas frente a 1 de 32, medido 2026-10-22)."""
    o = agente.opciones_contexto_stt()
    assert o["forward_chat_context"] is False
    assert o["keyterm_detection"] == {"enabled": False}  # usaría un LLM de la nube de LiveKit


def test_el_vad_termina_la_voz_antes_que_assemblyai_el_turno() -> None:
    """Para saber si el vecino sigue hablando cuando AssemblyAI cierra el turno, el VAD tiene que
    dar la voz por acabada antes que el silencio mínimo de AssemblyAI."""
    silencio_vad_ms = agente.opciones_vad()["min_silence_duration"] * 1000
    # Con margen: el VAD también tarda en decidir, y si aún oye el final de la frase al cerrarse
    # el turno se retiene un turno completo
    assert silencio_vad_ms <= settings.livekit_stt_silencio_min_ms / 2


def test_el_contexto_del_stt_se_puede_activar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "livekit_stt_contexto", True)
    assert agente.opciones_contexto_stt()["forward_chat_context"] is True
