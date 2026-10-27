"""Entrada por SIP (spec 2026-10-22-telnyx-sip-pilot-design): trunk y regla de LiveKit, quién
llama y a dónde se transfiere."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from livekit import api

from building_call.telephony import sip

PASSWORD = "clave-del-softphone-larga"


def _settings(**overrides: Any) -> SimpleNamespace:
    base = {
        "livekit_url": "ws://localhost:7880",
        "livekit_api_key": "devkey",
        "livekit_api_secret": "secret",
        "livekit_agent": "fincall",
        "sip_number": "+34900000000",
        "sip_auth_username": "softphone",
        "sip_auth_password": PASSWORD,
        "sip_allowed_addresses": "",
        "handoff_target": "",
    }
    return SimpleNamespace(**(base | overrides))


class FakeSip:
    """Lo justo de `SipService` para comprobar que la configuración es idempotente."""

    def __init__(self) -> None:
        self.trunks: list[api.SIPInboundTrunkInfo] = []
        self.rules: list[api.SIPDispatchRuleInfo] = []
        self.calls: list[str] = []

    async def list_inbound_trunk(self, _: Any) -> Any:
        return SimpleNamespace(items=list(self.trunks))

    async def create_inbound_trunk(self, req: api.CreateSIPInboundTrunkRequest) -> Any:
        self.calls.append("create_trunk")
        trunk = api.SIPInboundTrunkInfo()
        trunk.CopyFrom(req.trunk)
        trunk.sip_trunk_id = f"ST_{len(self.trunks)}"
        self.trunks.append(trunk)
        return trunk

    async def update_inbound_trunk(self, trunk_id: str, trunk: api.SIPInboundTrunkInfo) -> Any:
        self.calls.append("update_trunk")
        trunk.sip_trunk_id = trunk_id
        self.trunks = [trunk if t.sip_trunk_id == trunk_id else t for t in self.trunks]
        return trunk

    async def list_dispatch_rule(self, _: Any) -> Any:
        return SimpleNamespace(items=list(self.rules))

    async def create_dispatch_rule(self, req: api.CreateSIPDispatchRuleRequest) -> Any:
        self.calls.append("create_rule")
        rule = api.SIPDispatchRuleInfo()
        rule.CopyFrom(req.dispatch_rule)
        rule.sip_dispatch_rule_id = f"SDR_{len(self.rules)}"
        self.rules.append(rule)
        return rule

    async def update_dispatch_rule(self, rule_id: str, rule: api.SIPDispatchRuleInfo) -> Any:
        self.calls.append("update_rule")
        rule.sip_dispatch_rule_id = rule_id
        self.rules = [rule if r.sip_dispatch_rule_id == rule_id else r for r in self.rules]
        return rule


def test_ready_with_number_and_authentication() -> None:
    assert sip.not_ready_reason(_settings()) is None


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"livekit_url": ""}, "LiveKit"),
        ({"sip_number": ""}, "BC_SIP_NUMBER"),
        ({"sip_number": "900000000"}, "E.164"),
        # Un trunk sin usuario ni IPs permitidas acepta llamadas de cualquiera
        ({"sip_auth_username": "", "sip_auth_password": ""}, "abierto"),
        ({"sip_auth_password": "corta"}, "16"),
        ({"handoff_target": "+34600000000"}, "tel:"),
        # Transferir al número de entrada devuelve la llamada al bot
        ({"handoff_target": "tel:+34900000000"}, "bucle"),
    ],
)
def test_not_ready_explains_why(overrides: dict[str, Any], fragment: str) -> None:
    reason = sip.not_ready_reason(_settings(**overrides))
    assert reason and fragment in reason
    assert PASSWORD not in reason


def test_allowed_addresses_are_enough_without_a_user() -> None:
    s = _settings(sip_auth_username="", sip_auth_password="", sip_allowed_addresses="192.0.2.0/24")
    assert sip.not_ready_reason(s) is None
    assert list(sip.inbound_trunk(s).allowed_addresses) == ["192.0.2.0/24"]


def test_the_trunk_only_accepts_the_configured_number() -> None:
    trunk = sip.inbound_trunk(_settings())
    assert trunk.name == sip.TRUNK_NAME
    assert list(trunk.numbers) == ["+34900000000"]
    assert trunk.auth_username == "softphone" and trunk.auth_password == PASSWORD


def test_each_call_gets_its_room_and_the_configured_agent() -> None:
    rule = sip.dispatch_rule(_settings(livekit_agent="fincall-pruebas"), "ST_1")
    assert rule.name == sip.RULE_NAME
    assert list(rule.trunk_ids) == ["ST_1"]
    assert rule.rule.dispatch_rule_individual.room_prefix == sip.ROOM_PREFIX
    assert [a.agent_name for a in rule.room_config.agents] == ["fincall-pruebas"]


async def test_configure_creates_and_then_updates_without_duplicating() -> None:
    fake = FakeSip()
    first = await sip.configure(fake, _settings())
    assert fake.calls == ["create_trunk", "create_rule"]
    second = await sip.configure(fake, _settings(sip_number="+34900000001"))
    assert fake.calls[2:] == ["update_trunk", "update_rule"]
    assert len(fake.trunks) == 1 and len(fake.rules) == 1
    assert first["trunk_id"] == second["trunk_id"]
    assert list(fake.trunks[0].numbers) == ["+34900000001"]
    assert PASSWORD not in str(second)


def test_the_caller_comes_from_the_sip_attributes() -> None:
    attributes = {"sip.phoneNumber": "+34611222333", "sip.trunkPhoneNumber": "+34900000000"}
    assert sip.caller_from(attributes) == "+34611222333"
    assert sip.caller_from({}) is None


@pytest.mark.parametrize(
    ("result", "target", "expected"),
    [
        ("handed_to_administrator", "tel:+34600000000", "tel:+34600000000"),
        ("handed_to_administrator", "", None),
        ("incident_created", "tel:+34600000000", None),
        (None, "tel:+34600000000", None),
    ],
)
def test_only_a_handoff_to_a_person_is_transferred(
    result: str | None, target: str, expected: str | None
) -> None:
    assert sip.transfer_target(result, _settings(handoff_target=target)) == expected


def test_control_keeps_how_the_call_ended() -> None:
    from building_call.voice.control import CallControl

    control = CallControl("llam-1")
    control.new_turn(
        {
            "turn": 3,
            "finished": True,
            "bot_messages": ["Le paso con una persona."],
            "data": {"result": "handed_to_administrator"},
        }
    )
    assert control.result == "handed_to_administrator"
    control.new_turn({"turn": 1, "bot_messages": ["Hola"]})
    assert control.result is None
