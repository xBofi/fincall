"""Languages as configuration: registry, per-language parsing and formatting.

from building_call import i18n
with i18n.use("en"):
    i18n.parser().yes_no("yeah, that's right")  # True
"""

from building_call.i18n.models import Language
from building_call.i18n.parsing import Parser, parser_for
from building_call.i18n.registry import (
    SOURCE_LANGUAGE,
    UnknownLanguage,
    active_code,
    current,
    default_code,
    enabled,
    get,
    languages,
    normalize_code,
    supported,
    use,
)


def parser() -> Parser:
    """The deterministic rules of the active language."""
    return parser_for(active_code())


__all__ = [
    "SOURCE_LANGUAGE",
    "Language",
    "Parser",
    "UnknownLanguage",
    "active_code",
    "current",
    "default_code",
    "enabled",
    "get",
    "languages",
    "normalize_code",
    "parser",
    "parser_for",
    "supported",
    "use",
]
