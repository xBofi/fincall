"""The language registry: every `locales/*.toml` file is one language.

Adding a language means adding one file (providers, word lists, template translations); the
tests check that it translates every template with the same variables. The default language of
a call comes from `BC_LANGUAGE`; each call can pick another (Twilio number, stream parameter,
console choice) or switch after detection.

Which language is active is a context variable, set by `CallManager` around each turn: the
graph's nodes and `render` read it, so a node never needs the language threaded through
every helper (same idea as Django's `translation.activate`).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path

from building_call.i18n.models import Language

LOCALES_DIR = Path(__file__).with_name("locales")
#: The language the templates are written in, in code (`graph/templates.py`)
SOURCE_LANGUAGE = "es"


class UnknownLanguage(KeyError):
    pass


@cache
def languages() -> dict[str, Language]:
    """All the languages, by code, loaded and validated once."""
    found: dict[str, Language] = {}
    for path in sorted(LOCALES_DIR.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        language = Language.model_validate(data)
        if language.code != path.stem:
            raise ValueError(f"{path.name} declares code {language.code!r}")
        found[language.code] = language
    if SOURCE_LANGUAGE not in found:
        raise ValueError(f"The source language {SOURCE_LANGUAGE!r} has no locale file")
    return found


def supported() -> tuple[str, ...]:
    return tuple(languages())


def normalize_code(code: str | None) -> str | None:
    """'en-US' -> 'en', 'ES' -> 'es'; None if it is not a supported language."""
    if not code:
        return None
    base = code.strip().replace("_", "-").split("-")[0].lower()
    return base if base in languages() else None


def get(code: str | None = None) -> Language:
    """The language for `code` (any case, with or without region); the default one if None."""
    if code is None:
        return languages()[default_code()]
    normalized = normalize_code(code)
    if normalized is None:
        raise UnknownLanguage(code)
    return languages()[normalized]


def default_code() -> str:
    from building_call.settings import settings

    return normalize_code(settings.language) or SOURCE_LANGUAGE


_ACTIVE: ContextVar[str | None] = ContextVar("building_call_language", default=None)


def active_code() -> str:
    return _ACTIVE.get() or default_code()


def current() -> Language:
    return languages()[active_code()]


@contextmanager
def use(code: str | None) -> Iterator[Language]:
    """Makes `code` the active language inside the block (None = the default one)."""
    language = get(code)
    token = _ACTIVE.set(language.code)
    try:
        yield language
    finally:
        _ACTIVE.reset(token)
