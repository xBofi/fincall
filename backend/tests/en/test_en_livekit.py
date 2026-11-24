"""LiveKit: el agente habla con la API con su secreto y la consola pide su token de sala."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from session_helpers import make_session

from building_call.api.main import create_app
from building_call.graph.ledger import cut_by_words
from building_call.graph.nodes import Dependencies
from building_call.settings import settings
from building_call.voice.control import CallControl, only_fillers

SECRET = "secreto-del-agente-con-largo-suficiente-0123456789"


@pytest.fixture
def with_livekit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_secret", SECRET)
    monkeypatch.setattr(settings, "livekit_url", "ws://localhost:7880")
    monkeypatch.setattr(settings, "livekit_api_key", "devkey")
    monkeypatch.setattr(settings, "livekit_api_secret", "secret-de-desarrollo-con-largo-suficiente")


@pytest.fixture
async def app(deps: Dependencies, with_livekit: None):  # noqa: ARG001
    return create_app(deps, streaming_factory=None)


async def test_the_agent_talks_with_its_secret(app) -> None:  # type: ignore[no-untyped-def]
    headers = {"Authorization": f"Bearer {SECRET}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/calls", json={}, headers=headers)
        assert r.status_code == 201
        view = r.json()
        assert view["pieces"] and view["pieces"][-1]["importance"] == "question"
        r = await c.post(
            f"/api/calls/{view['call_id']}/messages",
            json={"text": "12 Main Street"},
            headers=headers,
        )
        assert r.status_code == 200 and r.json()["turn"] == 1


async def test_wrong_or_unset_secret_does_not_get_in(app, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/calls", json={}, headers={"Authorization": "Bearer otro"})
        assert r.status_code == 401
        monkeypatch.setattr(settings, "agent_secret", "")
        r = await c.post("/api/calls", json={}, headers={"Authorization": "Bearer "})
        assert r.status_code == 401


async def test_the_console_requests_a_room_token(app, deps: Dependencies) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/livekit/token", json={"call_id": "llam-1"})
        assert r.status_code == 401
        c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"call_id": "llam-1"})
        assert r.status_code == 200
        body = r.json()
        assert body["url"] == "ws://localhost:7880"
        assert body["room"].startswith("consola-")
        from livekit import api

        claims = api.TokenVerifier("devkey", "secret-de-desarrollo-con-largo-suficiente").verify(
            body["token"]
        )
        assert claims.video.room == body["room"] and claims.video.room_join
        dispatch = claims.room_config.agents[0]
        assert dispatch.agent_name == "fincall"
        assert '"call_id": "llam-1"' in dispatch.metadata


async def test_the_dispatch_uses_the_configured_agent_name(
    app, deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Otro agente (pruebas, banco) puede convivir con el de la consola en el mismo LiveKit."""
    monkeypatch.setattr(settings, "livekit_agent", "building-call-tests")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"call_id": "llam-1"})
        from livekit import api

        claims = api.TokenVerifier("devkey", "secret-de-desarrollo-con-largo-suficiente").verify(
            r.json()["token"]
        )
        assert claims.room_config.agents[0].agent_name == "building-call-tests"


async def test_token_without_livekit_configured(
    deps: Dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "livekit_api_key", "")
    app = create_app(deps, streaming_factory=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_session", make_session(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"call_id": "llam-1"})
        assert r.status_code == 503


# ----------------------------------------------------------------- lo que oyó, por palabras


def test_cut_by_words_ignores_punctuation_and_spaces() -> None:
    text = "Let me check I've got this right: the faucet is leaking. Is that correct?"
    # LiveKit devuelve las palabras sincronizadas con el audio, no el texto exacto
    assert text[: cut_by_words(text, "Let me check I've got this right the faucet")] == (
        "Let me check I've got this right: the faucet"
    )
    assert cut_by_words(text, "") == 0
    assert cut_by_words(
        text, "Let me check I've got this right: the faucet is leaking. Is that correct?"
    ) == len(text)


# ----------------------------------------------------------------- control de la llamada


def test_control_hangs_up_after_the_heard_closing() -> None:
    control = CallControl("llam-1")
    control.new_turn({"turn": 4, "finished": True, "bot_messages": ["Hasta luego."]})
    assert control.on_finished_speaking({"pieces": []}) == ("hang_up", [])


def test_control_repeats_the_cut_notice_before_hanging_up() -> None:
    control = CallControl("llam-1")
    control.new_turn({"turn": 4, "finished": True, "bot_messages": ["..."]})
    delivery = {
        "pieces": [
            {
                "importance": "notice",
                "state": "delivered",
                "text": "He registrado su incidencia.",
            },
            {
                "importance": "notice",
                "state": "not_heard",
                "text": "They will call you back at this number.",
            },
            {"importance": "courtesy", "state": "not_delivered", "text": "Hasta luego."},
        ]
    }
    assert control.on_finished_speaking(delivery) == (
        "repeat_and_hang_up",
        ["They will call you back at this number."],
    )


def test_control_continues_if_the_call_has_not_ended() -> None:
    control = CallControl("llam-1")
    control.new_turn({"turn": 1, "finished": False, "bot_messages": ["Is that right?"]})
    assert control.on_finished_speaking({"pieces": []}) == ("proceed", [])


async def test_the_agent_starts_interpretation_early_with_the_partial(
    app, deps: Dependencies
) -> None:  # type: ignore[no-untyped-def]
    headers = {"Authorization": f"Bearer {SECRET}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        call_id = (await c.post("/api/calls", json={}, headers=headers)).json()["call_id"]
        r = await c.post(
            f"/api/calls/{call_id}/partial", json={"text": "Vivo en la"}, headers=headers
        )
        assert r.status_code == 202
        assert deps.speculations.get(call_id)


async def test_the_agent_requests_the_estates_vocabulary(app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/voice/vocabulary", headers={"Authorization": f"Bearer {SECRET}"})
        assert r.status_code == 200
        assert "12 Main Street" in " ".join(r.json())


# ----------------------------------------------------------------- turnos partidos e interrumpidos


class _Msg:
    def __init__(self, id: str, role: str, text: str) -> None:  # noqa: A002
        self.id, self.role, self.text_content = id, role, text


class _FakeApi:
    """Registra el orden de las llamadas; `turn` tarda lo que se le diga."""

    def __init__(self, wait_s: float = 0.0) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.wait_s = wait_s
        self.n = 0

    async def turn(self, call_id: str, text: str) -> dict[str, Any]:
        self.calls.append(("turn", text))
        await asyncio.sleep(self.wait_s)
        self.n += 1
        return {"turn": self.n, "finished": False, "bot_messages": [f"respuesta {self.n}"]}

    async def delivery(
        self, call_id: str, turn: int, interrupted: bool, text: str, ratio: float
    ) -> dict[str, Any]:
        self.calls.append(("delivery", (turn, interrupted, text)))
        return {}


async def test_control_joins_what_the_caller_said_and_was_not_sent_yet() -> None:
    """AssemblyAI parte "Sí, eso es" en dos finales si el vecino hace una pausa: lo que no se
    mandó a la API va en un solo turno, y lo ya mandado no se repite. Lo que se quedó esperando
    (LiveKit canceló la generación antes de enviarlo) sigue pendiente."""
    api = _FakeApi()
    control = CallControl("llam-1")
    items = [_Msg("u1", "user", "Yes."), _Msg("a1", "assistant", "Is that right?")]
    assert control.to_send(items) == "Yes."
    await control.send_turn(api, "Yes.")
    items += [_Msg("u2", "user", "That's right.")]
    assert control.to_send(items) == "That's right."  # no se llegó a enviar
    items += [_Msg("u3", "user", "From the ceiling.")]
    assert control.to_send(items) == "That's right. From the ceiling."
    await control.send_turn(api, "That's right. From the ceiling.")
    assert control.to_send(items) == ""


async def test_a_turn_that_never_played_is_declared_before_the_next() -> None:
    """Si el vecino habla antes de que suene la respuesta, LiveKit no informa de esa respuesta.
    El grafo tiene que saber que no se oyó antes de interpretar lo siguiente (si no, un "eso
    es" confirmaría un resumen que el vecino no ha escuchado)."""
    api = _FakeApi()
    control = CallControl("llam-1")
    control.new_turn({"turn": 0, "bot_messages": ["Hi, which building?"]})
    control.report()  # el saludo sí sonó
    await control.send_turn(api, "12 Main Street")  # su respuesta no llega a sonar
    await control.send_turn(api, "yes")
    assert api.calls == [
        ("turn", "12 Main Street"),
        ("delivery", (1, True, "")),
        ("turn", "yes"),
    ]


async def test_what_played_is_not_declared_as_not_played() -> None:
    api = _FakeApi()
    control = CallControl("llam-1")
    await control.send_turn(api, "12 Main Street")
    assert control.report() == (1, "respuesta 1")  # LiveKit avisa de que sonó
    await control.send_turn(api, "yes")
    assert [x[0] for x in api.calls] == ["turn", "turn"]


def _sequence(*states: bool):  # type: ignore[no-untyped-def]
    """`hablando()` que devuelve estos estados y luego repite el último."""
    remaining = list(states)

    def speaking() -> bool:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return speaking


async def test_if_the_caller_keeps_talking_when_the_turn_closes_it_waits() -> None:
    """AssemblyAI cerró el turno en una pausa pero el vecino ya ha vuelto a hablar: no se manda
    nada hasta que lleve un rato callado. Las pausas cortas (comas) no cuentan. Si en ese tiempo
    llega su siguiente turno, LiveKit cancela esta espera y el texto va junto con el siguiente
    (`to_send`)."""
    control = CallControl("llam-1")
    control.on_turn_closed(caller_speaking=True)
    states = [True] * 5 + [False] * 2 + [True] * 5 + [False]
    queries = 0

    def speaking() -> bool:
        nonlocal queries
        queries += 1
        return states[min(queries, len(states)) - 1]

    wait = control.wait_before_sending("Yes.", speaking, final_wait_s=0.05, step_s=0.01)
    assert await wait is True
    assert queries >= len(states)  # la pausa de dos consultas no acabó la espera
    # El cierre ya se ha consumido: el turno siguiente no espera
    assert await control.wait_before_sending("Yes.", speaking) is False


async def test_a_silent_close_with_content_waits_for_nothing() -> None:
    control = CallControl("llam-1")
    control.on_turn_closed(caller_speaking=False)

    def nunca() -> bool:
        raise AssertionError("no debe consultarse")

    assert await control.wait_before_sending("Floor 1, apartment B.", nunca) is False


async def test_the_wait_is_capped_if_the_noise_does_not_stop() -> None:
    control = CallControl("llam-1")
    control.on_turn_closed(caller_speaking=True)
    t0 = asyncio.get_running_loop().time()
    wait = control.wait_before_sending(
        "Yes.", lambda: True, max_s=0.05, final_wait_s=0, step_s=0.01
    )
    assert await wait is True
    assert asyncio.get_running_loop().time() - t0 < 0.5


async def test_only_fillers_waits_to_see_if_the_caller_continues() -> None:
    """ "Oh, okay, sorry." y una pausa: lo normal es que siga ("pues piso uno, puerta A"). Se
    da un margen corto para que vuelva a hablar; si no habla, se manda tal cual."""
    control = CallControl("llam-1")
    speaking = _sequence(False, False, True, True, False)
    assert await control.wait_before_sending(
        "Oh, okay, sorry.", speaking, final_wait_s=0, step_s=0.001
    )
    t0 = asyncio.get_running_loop().time()
    silent = control.wait_before_sending("Um.", lambda: False, filler_wait_s=0.05)
    assert await silent is True
    assert asyncio.get_running_loop().time() - t0 < 0.5


async def test_when_the_caller_goes_quiet_their_turn_is_given_time_to_arrive() -> None:
    """El VAD da la voz por acabada antes de que AssemblyAI cierre el turno: si se mandara en
    ese instante, el resto llegaría como otro turno."""
    control = CallControl("llam-1")
    control.on_turn_closed(caller_speaking=True)
    t0 = asyncio.get_running_loop().time()
    wait = control.wait_before_sending("Yes.", _sequence(True, False), final_wait_s=0.05)
    assert await wait is True
    assert asyncio.get_running_loop().time() - t0 >= 0.05


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Oh, okay, sorry.", True),
        ("Um.", True),
        ("Hello, hi.", True),
        ("So...", True),
        ("Yes.", False),  # una respuesta, no una muletilla
        ("Well, yes.", False),
        ("Okay.", False),  # on its own it is a "yes"
        ("Well.", False),
        ("Okay, okay.", False),  # repetido sigue siendo un "yes"
        ("Well, okay.", False),
        ("A.", False),  # the letter of an apartment
        ("B.", False),
        ("Let me see...", True),
        ("I mean...", True),
        ("No.", False),
        ("Floor 1, apartment.", False),
        ("", False),
    ],
)
def test_only_fillers(text: str, expected: bool) -> None:
    assert only_fillers(text) is expected


async def test_turns_stay_in_order_even_if_livekit_cancels_the_first() -> None:
    """LiveKit cancela la generación en curso cuando llega otro turno del vecino. La petición a
    la API no se corta (el grafo ya la está procesando) y la siguiente espera a que acabe."""
    api = _FakeApi(wait_s=0.05)
    control = CallControl("llam-1")
    primero = asyncio.ensure_future(control.send_turn(api, "Yes."))
    await asyncio.sleep(0.01)
    primero.cancel()
    view = await control.send_turn(api, "That's right.")
    turns = [x for x in api.calls if x[0] == "turn"]
    assert turns == [("turn", "Yes."), ("turn", "That's right.")]
    assert ("delivery", (1, True, "")) in api.calls  # la respuesta 1 nunca sonó
    assert view["turn"] == 2 and control.turn == 2 and control.text == "respuesta 2"


async def test_a_cancelled_failing_turn_is_logged_and_does_not_block_the_next(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Si LiveKit cancela la generación y luego la API falla, nadie espera ya esa petición: el
    error se registra (no se pierde en silencio) y el turno siguiente sale igual."""

    class _FailingApi(_FakeApi):
        async def turn(self, call_id: str, text: str) -> dict[str, Any]:
            if text == "Yes.":
                await asyncio.sleep(0.02)
                raise RuntimeError("la API no responde")
            return await super().turn(call_id, text)

    api = _FailingApi()
    control = CallControl("llam-1")
    primero = asyncio.ensure_future(control.send_turn(api, "Yes."))
    await asyncio.sleep(0.005)
    primero.cancel()
    with caplog.at_level("ERROR", logger="building_call.voice.control"):
        view = await control.send_turn(api, "That's right.")
    assert view["bot_messages"] == ["respuesta 1"]
    own = [r for r in caplog.records if r.name == "building_call.voice.control"]
    assert any(r.exc_info for r in own)
