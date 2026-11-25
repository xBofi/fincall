"""Is what the recognizer hears the bot itself, playing through the caller's speaker?

In hands-free mode the assistant's voice comes back through the microphone. If that cut the
bot, it would stop itself mid-sentence (it happened with the console's volume-based cut). The cut
is decided by words, as on the phone, and words that are already in what the bot is saying right
now do not count: they are echo.

Language-agnostic: both texts go through the script-aware `basic_normalize` of the deterministic
paths (no case, punctuation, Latin accents or Arabic diacritics).
"""

from __future__ import annotations

from building_call.i18n.text import basic_normalize

#: Share of the heard words that must be in the bot's message to take it as echo
ECHO_SHARE = 0.6


def _words(text: str) -> list[str]:
    return basic_normalize(text).split()


def is_echo(heard: str, bot_text: str) -> bool:
    """Echo if nearly all of what was heard is in the bot's message. The recognizer accumulates
    the turn's text ("he identificado calle mayor... no perdone, es la catorce"): as soon as there
    are two words the bot is not saying, it is the caller, even if the start was echo."""
    words = _words(heard)
    if not words or not bot_text:
        return False
    bot = set(_words(bot_text))
    own = sum(w in bot for w in words)
    return own / len(words) >= ECHO_SHARE and len(words) - own < 2
