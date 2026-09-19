"""Settings.from_env: prefijo ESCANER_, defaults, parseo de CSV para allowed_product_hosts,
user_agent derivado de contact, y que un tax_profile inválido truene temprano (no a medio scan)."""

from __future__ import annotations

import pydantic
import pytest

from escaner.config import DEFAULT_ALLOWED_HOSTS, Settings


def test_defaults_when_env_is_empty():
    s = Settings.from_env({})
    assert s.min_roi == 0.15
    assert s.min_profit == 150.0
    assert s.match_threshold == 0.90
    assert s.review_threshold == 0.60
    assert s.undercut == 0.02
    assert s.min_comparables == 3
    assert s.min_host_interval_s == 10.0
    assert s.tax_profile == "plataformas"
    assert s.reputation == "green"
    assert s.listing_type == "gold_special"
    assert s.default_weight_g == 1000
    assert s.allowed_product_hosts == DEFAULT_ALLOWED_HOSTS
    assert "contacto@ejemplo.com" in s.user_agent


def test_env_overrides_prefixed_values():
    env = {
        "ESCANER_MIN_ROI": "0.25",
        "ESCANER_MIN_PROFIT": "300",
        "ESCANER_TAX_PROFILE": "sin_rfc",
        "ESCANER_KEEPA_KEY": "abc123",
        "ESCANER_LLM_ENABLED": "true",
        "NOT_ESCANER_PREFIXED": "ignoreme",
    }
    s = Settings.from_env(env)
    assert s.min_roi == 0.25
    assert s.min_profit == 300.0
    assert s.tax_profile == "sin_rfc"
    assert s.keepa_key == "abc123"
    assert s.llm_enabled is True


def test_allowed_product_hosts_parsed_from_csv_string():
    s = Settings.from_env({"ESCANER_ALLOWED_PRODUCT_HOSTS": "costco.com.mx, homedepot.com.mx ,,"})
    assert s.allowed_product_hosts == ("costco.com.mx", "homedepot.com.mx")


def test_contact_flows_into_derived_user_agent_when_no_explicit_user_agent():
    s = Settings.from_env({"ESCANER_CONTACT": "yo@midominio.mx"})
    assert "yo@midominio.mx" in s.user_agent
    assert s.user_agent.startswith("escaner-arbitraje/")


def test_explicit_user_agent_is_not_overridden():
    s = Settings.from_env({"ESCANER_USER_AGENT": "mi-bot-propio/9.9"})
    assert s.user_agent == "mi-bot-propio/9.9"


def test_invalid_tax_profile_raises():
    with pytest.raises(ValueError, match="tax_profile"):
        Settings.from_env({"ESCANER_TAX_PROFILE": "inventado"})


def test_tax_property_resolves_the_preset():
    s = Settings.from_env({"ESCANER_TAX_PROFILE": "resico"})
    assert s.tax.name == "resico"


def test_settings_is_frozen():
    s = Settings.from_env({})
    with pytest.raises(pydantic.ValidationError):  # modelo frozen: no se puede reasignar
        s.min_roi = 0.99
