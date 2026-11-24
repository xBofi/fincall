"""Dates, numbers and money as each language writes and says them (CLDR data via Babel).

US English gets US conventions (`11/24/2026`, `$1,250.50`), Spanish and Catalan European ones
(`24/11/2026`, `1250,50 €`), Arabic, Hindi and Urdu their locale's (Arabic-script or Latin
digits as CLDR says for `ar-AE`, Indian digit grouping for `hi-IN`: `1,25,000`).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from babel.dates import format_date as _babel_date
from babel.dates import format_time as _babel_time
from babel.numbers import format_currency as _babel_currency
from babel.numbers import format_decimal

from building_call.i18n import registry


def _locale(language: str | None) -> str:
    return registry.get(language or registry.active_code()).locale.replace("-", "_")


def format_date(value: date | datetime, language: str | None = None, style: str = "medium") -> str:
    """'Nov 24, 2026' (en-US), '24 nov 2026' (es-ES)… `style`: short | medium | long | full."""
    return _babel_date(value, format=style, locale=_locale(language))


def format_time(value: time | datetime, language: str | None = None) -> str:
    """'3:05 PM' (en-US), '15:05' (es-ES)."""
    return _babel_time(value, format="short", locale=_locale(language))


def format_number(value: float | int | Decimal, language: str | None = None) -> str:
    """'1,250.5' (en-US), '1250,5' (es-ES), '1,25,000' (hi-IN)."""
    return format_decimal(value, locale=_locale(language))


def format_currency(
    amount: float | int | Decimal, language: str | None = None, currency: str | None = None
) -> str:
    """The amount in the language's currency (USD for en-US, EUR for es-ES…) unless one is
    given: '$1,250.50', '1250,50 €', 'AED 1,250.50'."""
    spec = registry.get(language or registry.active_code())
    return _babel_currency(amount, currency or spec.formats.currency, locale=_locale(language))
