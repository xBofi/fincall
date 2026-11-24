"""Entrada de llamadas por SIP: trunk de LiveKit, regla de despacho y transferencia a humano.

El operador (Telnyx; en local, un softphone) entrega la llamada a `livekit-sip`, que la mete en
una sala `tel-…` con el agente (`building_call.voice.agent`). El agente es el mismo que el de la
consola: aquí solo está lo que cambia por venir de un teléfono.
Spec: `docs/superpowers/specs/2026-10-22-telnyx-sip-pilot-design.md`.
"""

from __future__ import annotations

import re
from typing import Any

from livekit import api

TRUNK_NAME = "fincall-entrada"
RULE_NAME = "fincall-llamadas"
ROOM_PREFIX = "tel-"

#: Con menos, un trunk con usuario es casi tan abierto como uno sin él
MIN_PASSWORD_CHARS = 16

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def _allowed_addresses(settings: Any) -> list[str]:
    return [a.strip() for a in settings.sip_allowed_addresses.split(",") if a.strip()]


def not_ready_reason(settings: Any) -> str | None:
    """Explica (sin secretos) por qué aún no se puede configurar la entrada SIP."""
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return "LiveKit no está configurado (BC_LIVEKIT_URL, BC_LIVEKIT_API_KEY/SECRET)"
    if not settings.sip_number:
        return "Falta BC_SIP_NUMBER (el número al que llaman, en local uno ficticio)"
    if not _E164.match(settings.sip_number):
        return "BC_SIP_NUMBER debe ir en formato E.164 (+34…)"
    has_user = bool(settings.sip_auth_username or settings.sip_auth_password)
    if not has_user and not _allowed_addresses(settings):
        return (
            "El trunk quedaría abierto a cualquiera: define BC_SIP_AUTH_USERNAME/PASSWORD "
            "o BC_SIP_ALLOWED_ADDRESSES"
        )
    if has_user and len(settings.sip_auth_password) < MIN_PASSWORD_CHARS:
        return f"BC_SIP_AUTH_PASSWORD debe tener al menos {MIN_PASSWORD_CHARS} caracteres"
    target = settings.handoff_target
    if target and not target.startswith(("tel:", "sip:")):
        return "BC_HANDOFF_TARGET debe empezar por tel: o sip:"
    if target and settings.sip_number in target:
        return "BC_HANDOFF_TARGET no puede ser el número de entrada: la llamada haría bucle"
    return None


def inbound_trunk(settings: Any) -> api.SIPInboundTrunkInfo:
    return api.SIPInboundTrunkInfo(
        name=TRUNK_NAME,
        numbers=[settings.sip_number],
        allowed_addresses=_allowed_addresses(settings),
        auth_username=settings.sip_auth_username,
        auth_password=settings.sip_auth_password,
    )


def dispatch_rule(settings: Any, trunk_id: str) -> api.SIPDispatchRuleInfo:
    """Una sala por llamada, con el agente ya pedido: no hay que esperar a que nadie lo despache."""
    return api.SIPDispatchRuleInfo(
        name=RULE_NAME,
        trunk_ids=[trunk_id],
        rule=api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
        ),
        room_config=api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name=settings.livekit_agent)]
        ),
    )


async def configure(sip: Any, settings: Any) -> dict[str, Any]:
    """Crea o actualiza (por nombre) el trunk y la regla. Idempotente; no devuelve secretos."""
    trunk = inbound_trunk(settings)
    trunks = (await sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())).items
    existing = next((t for t in trunks if t.name == TRUNK_NAME), None)
    if existing:
        saved = await sip.update_inbound_trunk(existing.sip_trunk_id, trunk)
    else:
        saved = await sip.create_inbound_trunk(api.CreateSIPInboundTrunkRequest(trunk=trunk))
    trunk_id = saved.sip_trunk_id

    rule = dispatch_rule(settings, trunk_id)
    rules = (await sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())).items
    existing_rule = next((r for r in rules if r.name == RULE_NAME), None)
    if existing_rule:
        saved_rule = await sip.update_dispatch_rule(existing_rule.sip_dispatch_rule_id, rule)
    else:
        saved_rule = await sip.create_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(dispatch_rule=rule)
        )
    return {
        "trunk_id": trunk_id,
        "rule_id": saved_rule.sip_dispatch_rule_id,
        "number": settings.sip_number,
        "agent": settings.livekit_agent,
        "authentication": "usuario" if settings.sip_auth_username else "IP",
    }


def caller_from(attributes: dict[str, str]) -> str | None:
    """El número del vecino, tal como lo entrega el operador (puede faltar si lo oculta)."""
    return attributes.get("sip.phoneNumber") or None


def transfer_target(result: str | None, settings: Any) -> str | None:
    """A dónde pasar la llamada al terminar, o None para colgar como siempre."""
    if result == "handed_to_administrator" and settings.handoff_target:
        return str(settings.handoff_target)
    return None
