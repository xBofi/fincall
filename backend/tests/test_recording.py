"""Grabación alineada de la llamada: línea de tiempo común, mezcla y voz del bot en la consola web."""

from __future__ import annotations

import struct
from typing import Any

import pytest
from conftest import make_session
from fastapi.testclient import TestClient
from test_streaming import FakeSession

from building_call.api.main import create_app
from building_call.graph.nodes import Dependencies
from building_call.recording import ConversationRecorder
from building_call.settings import settings


class Clock:
    """Reloj manual: la colocación en el tiempo se prueba sin dormir."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def pcm(ms: int, rate: int = 16000, value: int = 10000) -> bytes:
    return struct.pack("<h", value) * int(rate * ms / 1000)


def samples(data: bytes) -> tuple[int, ...]:
    return struct.unpack(f"<{len(data) // 2}h", data)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def recorder(clock: Clock) -> ConversationRecorder:
    return ConversationRecorder(clock=clock)


# ---------------------------------------------------------------- línea de tiempo


def test_jitter_does_not_stretch_the_track(recorder: ConversationRecorder, clock: Clock) -> None:
    """Los trozos llegan con retraso variable, pero el audio es continuo: se escribe seguido."""
    clock.advance(0.15)
    recorder.write_client(pcm(100))
    clock.advance(0.20)
    recorder.write_client(pcm(100))
    assert recorder.duration_ms() == 200


def test_a_real_gap_is_filled_with_silence(
    recorder: ConversationRecorder, clock: Clock
) -> None:
    """Dos segundos sin audio (el vecino calla mientras habla el bot) siguen ocupando su sitio."""
    recorder.write_client(pcm(100))
    clock.advance(2.0)
    recorder.write_client(pcm(100))
    assert recorder.duration_ms() == 2100
    track = recorder.tracks()["client"]
    assert set(samples(track[3200:32000])) == {0}


def test_the_assistant_is_anchored_to_the_mark(recorder: ConversationRecorder, clock: Clock) -> None:
    """El bot se sintetiza al cerrar, pero se coloca donde empezó a sonar."""
    clock.advance(1.0)
    brand = recorder.mark()
    assert brand == 32000  # 1 s a 16 kHz PCM16
    recorder.write_assistant(pcm(200), since=brand)
    track = recorder.tracks()["assistant"]
    assert set(samples(track[:32000])) == {0}
    assert len(track) == 32000 + 6400


def test_a_bot_turn_does_not_overwrite_the_previous_one(recorder: ConversationRecorder) -> None:
    """Si una marca cae dentro de un turno ya escrito, el nuevo va detrás en lugar de encima."""
    recorder.write_assistant(pcm(200), since=0)
    recorder.write_assistant(pcm(100), since=3200)
    track = recorder.tracks()["assistant"]
    assert len(track) == 6400 + 3200
    assert set(samples(track)) == {10000}  # nada se sobrescribió con silencio


# ---------------------------------------------------------------- mezcla


def test_the_mix_sums_both_voices_and_clips(recorder: ConversationRecorder) -> None:
    """Si coinciden dos voces fuertes, recorta en el límite de int16; nunca da la vuelta."""
    recorder.write_client(pcm(100, value=20000))
    recorder.write_assistant(pcm(100, value=20000), since=0)
    assert set(samples(recorder.mix())) == {32767}


def test_the_mix_lasts_as_long_as_the_longest_track(recorder: ConversationRecorder) -> None:
    recorder.write_client(pcm(200))
    recorder.write_assistant(pcm(50), since=0)
    assert len(recorder.mix()) == 6400


def test_tracks_puts_the_mix_first_and_skips_empty_ones(
    recorder: ConversationRecorder,
) -> None:
    assert recorder.tracks() == {}
    recorder.write_client(pcm(100))
    assert list(recorder.tracks()) == ["mix", "client"]  # sin `assistant`, que está vacía


def test_everything_is_saved_at_16k_wherever_it_comes_from(recorder: ConversationRecorder) -> None:
    """El teléfono entrega 8 kHz y Piper 22,05 kHz; la pista siempre sale a 16 kHz."""
    recorder.write_client(pcm(100, rate=8000), 8000)
    recorder.write_assistant(pcm(100, rate=22050), 22050, since=0)
    assert abs(recorder.duration_ms() - 100) <= 5  # ratecv redondea


# ---------------------------------------------------------------- consola web


class ServerTts:
    """TTS de servidor falso: devuelve 100 ms de tono por frase y apunta lo que le piden."""

    name = "falso/servidor"
    rate = 16000

    def __init__(self) -> None:
        self.phrases: list[str] = []

    def available(self) -> bool:
        return True

    async def synthesize(self, text: str) -> bytes:
        self.phrases.append(text)
        return pcm(100)


class MissingTts(ServerTts):
    def available(self) -> bool:
        return False


def _client(deps: Dependencies, tts: Any, tmp_path: Any, monkeypatch: Any) -> TestClient:
    monkeypatch.setattr(settings, "audio_dir", str(tmp_path))
    monkeypatch.setattr("building_call.api.main.create_tts", lambda _s: tts)
    c = TestClient(create_app(deps, streaming_factory=FakeSession))
    c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
    return c


def _conversation(client: TestClient) -> str:
    """Un turno completo como lo haría el navegador: audio, respuesta, pausa y ledger."""
    call_id = client.post("/api/calls", json={}).json()["call_id"]
    with client.websocket_connect(f"/api/calls/{call_id}/voice?sample_rate=16000") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(pcm(200))
        ws.send_json({"type": "end_turn"})
        ws.receive_json()  # parcial
        ws.receive_json()  # final
        reply = ws.receive_json()
        assert reply["type"] == "reply"
        # El navegador silencia el micro, habla, y luego informa de lo que llegó a sonar
        ws.send_json({"type": "pause"})
        ws.send_bytes(pcm(200))  # el vecino sigue sonando mientras habla el bot
        ws.send_json(
            {
                "type": "delivery",
                "turn": reply["turn"],
                "state": "completed",
                "ratio": 1.0,
                "delivered_text": "Gracias, tomo nota.",
            }
        )
        ws.send_json({"type": "resume"})
    return str(call_id)


async def test_the_web_console_also_records_the_assistant(
    deps: Dependencies, tmp_path: Any, monkeypatch: Any
) -> None:
    """El bot habla por el navegador: su pista se reconstruye con el TTS de servidor.

    Antes de 2026-08-29 el expediente solo guardaba `client` y sonaba como un monólogo.
    """
    tts = ServerTts()
    call_id = _conversation(_client(deps, tts, tmp_path, monkeypatch))

    tracks = await deps.repo.list_tracks(call_id)  # type: ignore[attr-defined]
    assert sorted(p["channel"] for p in tracks) == ["assistant", "client", "mix"]
    # Se sintetizó el saludo del turno 0 y el texto que el navegador dijo haber entregado
    assert "Gracias, tomo nota." in tts.phrases
    assert len(tts.phrases) == 2

    by_channel = {p["channel"]: p for p in tracks}
    assert by_channel["mix"]["duration_ms"] >= by_channel["client"]["duration_ms"]


async def test_the_caller_is_recorded_also_while_the_bot_speaks(
    deps: Dependencies, tmp_path: Any, monkeypatch: Any
) -> None:
    """Los 200 ms enviados en pausa cuentan: si se descartan, la pista pierde el barge-in y los
    tiempos dejan de cuadrar con la mezcla."""
    call_id = _conversation(_client(deps, ServerTts(), tmp_path, monkeypatch))
    tracks = {p["channel"]: p for p in await deps.repo.list_tracks(call_id)}  # type: ignore[attr-defined]
    assert tracks["client"]["duration_ms"] >= 400


async def test_without_server_tts_the_caller_voice_is_still_saved(
    deps: Dependencies, tmp_path: Any, monkeypatch: Any
) -> None:
    """Degradación: sin TTS no hay pista del asistente, pero no se pierde la llamada."""
    call_id = _conversation(_client(deps, MissingTts(), tmp_path, monkeypatch))
    tracks = await deps.repo.list_tracks(call_id)  # type: ignore[attr-defined]
    assert sorted(p["channel"] for p in tracks) == ["client", "mix"]
