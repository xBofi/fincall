"""Voice personas: how one client's agent sounds, per language.

An agency runs the same agent for many clients; each one wants its own name, voice, tone and
greeting. A persona is one TOML file in `personas/` (`<id>.toml`) with, per language:

- `agent_name`: the name the agent introduces itself with;
- `voice`: ElevenLabs voice ID for that language (empty = the language's or the default voice);
- `tone`: one line of style for the phrasing model ("warm and brief, first names");
- `templates`: overrides of editable templates (the greeting, usually), same contract as the
  console's: same variables, a question where the template asks one;
- `faq`: answers by FAQ id (`who` = "who are you?").

What a persona never changes: the flow, the facts the agent must say (the phrasing check still
applies) and the legal notices' meaning (the greeting must still say it is an AI and recorded).

The persona of a call is chosen at its start (Twilio number, console, `BC_PERSONA`) and is
active, like the language, around each turn (`use`).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

PERSONAS_DIR = Path(__file__).with_name("personas")


class PersonaLanguage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str
    voice: str = ""
    tone: str = ""
    templates: dict[str, str] = Field(default_factory=dict)
    faq: dict[str, str] = Field(default_factory=dict)


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    #: The client (property manager) the persona belongs to, for the console
    client: str
    languages: dict[str, PersonaLanguage]


class UnknownPersona(KeyError):
    pass


@cache
def personas() -> dict[str, Persona]:
    found: dict[str, Persona] = {}
    for path in sorted(PERSONAS_DIR.glob("*.toml")):
        persona = Persona.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
        if persona.id != path.stem:
            raise ValueError(f"{path.name} declares id {persona.id!r}")
        found[persona.id] = persona
    return found


def get(persona_id: str) -> Persona:
    try:
        return personas()[persona_id]
    except KeyError as e:
        raise UnknownPersona(persona_id) from e


def default_id() -> str | None:
    from building_call.settings import settings

    return settings.persona if settings.persona in personas() else None


_ACTIVE: ContextVar[str | None] = ContextVar("building_call_persona", default=None)


@contextmanager
def use(persona_id: str | None) -> Iterator[None]:
    """Makes `persona_id` the active persona inside the block (None = no persona)."""
    if persona_id is not None:
        get(persona_id)  # fail early on a typo
    token = _ACTIVE.set(persona_id)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def active(language: str) -> PersonaLanguage | None:
    """The active persona's settings for `language`, if it has any."""
    persona_id = _ACTIVE.get()
    if persona_id is None:
        return None
    return personas()[persona_id].languages.get(language)


def for_call(persona_id: str | None, language: str) -> PersonaLanguage | None:
    """Outside the graph (telephony, TTS): the call's persona for its language."""
    if not persona_id or persona_id not in personas():
        return None
    return personas()[persona_id].languages.get(language)
