import pytest
from pydantic import ValidationError

from building_call.domain.config import (
    DEFAULTS,
    NotificationRule,
    PartialConfig,
    resolve,
)


def test_without_layers_returns_defaults() -> None:
    assert resolve() == DEFAULTS


def test_estate_overrides_administrator_and_defaults() -> None:
    admin = PartialConfig(home_attempts=5, specialist_by_category={"plumbing": "f1"})
    estate = PartialConfig(home_attempts=2, graph="with_night_schedule")
    cfg = resolve(admin, estate)
    assert cfg.home_attempts == 2
    assert cfg.graph == "with_night_schedule"
    assert cfg.specialist_by_category == {"plumbing": "f1"}
    assert cfg.estate_attempts == DEFAULTS.estate_attempts


def test_missing_field_inherits_global_default() -> None:
    cfg = resolve(PartialConfig(), PartialConfig())
    assert cfg.category_catalog == DEFAULTS.category_catalog


def test_specialist_outside_catalog_is_an_error() -> None:
    with pytest.raises(ValidationError, match="fuera del catálogo"):
        resolve(PartialConfig(specialist_by_category={"piscina": "p1"}))


def test_emergency_is_treated_as_high_until_enabled() -> None:
    assert DEFAULTS.effective_urgency("emergency") == "high"
    active = resolve(PartialConfig(emergencies_enabled=True))
    assert active.effective_urgency("emergency") == "emergency"


def test_rule_for_uses_effective_urgency() -> None:
    cfg = resolve(
        PartialConfig(
            notice_rules=[NotificationRule(urgency="high", channel="call", target="administrador")]
        )
    )
    rule = cfg.rule_for("emergency")
    assert rule is not None and rule.channel == "call"


def test_address_similarity_with_numbers_in_words() -> None:
    from building_call.graph.nodes import _address_similarity

    assert _address_similarity("Vivo en la calle Mayor doce", "Calle Mayor 12") >= 0.6
    assert _address_similarity("avenida alameda treinta y dos", "Avenida de la Alameda 32") >= 0.6
    assert _address_similarity("Plaza Inexistente 99", "Calle Mayor 12") < 0.6


def test_home_regex_and_normalisation_of_numbers_in_words() -> None:
    from building_call.graph.nodes import _extract_home_regex, _normalize_entrance

    assert _normalize_entrance("dos") == "2"
    assert _extract_home_regex("portal dos, segundo C") == ("2", "2", "C")
    assert _extract_home_regex("en el tercero B") == (None, "3", "B")
    assert _extract_home_regex("3º A") == (None, "3", "A")
