"""Tests for config loading and SecretsConfig."""

import textwrap
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from kalshi_agent_trader.config import (
    AppConfig,
    ComplianceConfig,
    RelativeValueConfig,
    RiskConfig,
    RuntimeConfig,
    SecretsConfig,
    SportsbookScrapeConfig,
    load_config,
)
from kalshi_agent_trader.cli.common import resolve_dry_run


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def test_load_config_compliance(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        compliance:
          allowed_categories: [Politics, Sports]
          prohibited_categories: [Financials]
          default_deny_unknown: true
          prohibited_keywords: [earnings]
        risk:
          dry_run: true
          max_total_exposure_usd: 50.0
          max_per_position_usd: 10.0
          max_contracts_per_order: 100
          daily_loss_cap_usd: 5.0
          min_confidence: 0.6
          min_edge: 0.05
        runtime:
          request_timeout_s: 10
          max_requests_per_second: 5
        relative_value:
          min_edge: 0.03
          min_match_confidence: 0.8
          allowed_sources: [polymarket]
        sportsbook_scrape:
          enabled: true
          require_quote_for_agent: true
          market_urls:
            KXTEST:
              - source: draftkings
                url: https://example.test/event
                outcome: Team A
                side: yes
        """,
    )
    cfg = load_config(str(p))
    assert isinstance(cfg, AppConfig)
    assert "Politics" in cfg.compliance.allowed_categories
    assert cfg.risk.dry_run is True
    assert cfg.risk.max_total_exposure_usd == Decimal("50.0")
    assert cfg.risk.min_confidence == pytest.approx(0.6)
    assert cfg.runtime.request_timeout_s == 10
    assert cfg.relative_value.min_edge == Decimal("0.03")
    assert cfg.relative_value.min_match_confidence == pytest.approx(0.8)
    assert cfg.sportsbook_scrape.enabled is True
    assert cfg.sportsbook_scrape.require_quote_for_agent is True
    assert cfg.sportsbook_scrape.market_urls["KXTEST"][0].source == "draftkings"


def test_load_config_defaults_on_empty_sections(tmp_path):
    p = _write_yaml(tmp_path, "{}")
    cfg = load_config(str(p))
    assert cfg.compliance.allowed_categories == []
    assert cfg.risk.dry_run is True
    assert cfg.runtime.max_requests_per_second == pytest.approx(8.0)


def test_models_config_defaults(tmp_path):
    cfg = load_config(str(_write_yaml(tmp_path, "{}")))
    assert "haiku" in cfg.models.scout_model
    assert "sonnet" in cfg.models.analyst_model


def test_models_config_override(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
        models:
          scout_model: claude-haiku-4-5-20251001
          analyst_model: claude-sonnet-4-6
        """,
    )
    cfg = load_config(str(p))
    assert cfg.models.scout_model == "claude-haiku-4-5-20251001"
    assert cfg.models.analyst_model == "claude-sonnet-4-6"


def test_risk_config_decimal_coercion():
    r = RiskConfig(max_total_exposure_usd=100.5)
    assert r.max_total_exposure_usd == Decimal("100.5")


def test_resolve_dry_run_cli_override_precedence():
    assert resolve_dry_run(False) is False
    assert resolve_dry_run(True) is True
    assert resolve_dry_run(True, live=True) is False
    assert resolve_dry_run(False, dry_run_override=True) is True
    assert resolve_dry_run(True, live=True, dry_run_override=True) is True
    assert resolve_dry_run(False, dry_run_override=False) is False


def test_relative_value_config_decimal_coercion():
    rv = RelativeValueConfig(min_edge=0.015, max_spread=0.12)
    assert rv.min_edge == Decimal("0.015")
    assert rv.max_spread == Decimal("0.12")


def test_sportsbook_scrape_config_defaults_to_disabled():
    cfg = SportsbookScrapeConfig()
    assert cfg.enabled is False
    assert cfg.market_urls == {}


def test_secrets_require_kalshi_raises_without_key():
    s = SecretsConfig(kalshi_api_key_id=None, kalshi_private_key_path=None)
    with pytest.raises(RuntimeError, match="KALSHI_API_KEY_ID"):
        s.require_kalshi()


def test_secrets_require_kalshi_raises_without_path():
    s = SecretsConfig(kalshi_api_key_id="some-key", kalshi_private_key_path=None)
    with pytest.raises(RuntimeError, match="KALSHI_PRIVATE_KEY_PATH"):
        s.require_kalshi()


def test_secrets_require_kalshi_passes_when_set():
    s = SecretsConfig(kalshi_api_key_id="key", kalshi_private_key_path="/some/path.pem")
    s.require_kalshi()  # should not raise
