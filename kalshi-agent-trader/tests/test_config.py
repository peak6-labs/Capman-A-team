"""Tests for config loading and SecretsConfig."""

import textwrap
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from kalshi_agent_trader.config import (
    AppConfig,
    ComplianceConfig,
    RiskConfig,
    RuntimeConfig,
    SecretsConfig,
    load_config,
)


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
        """,
    )
    cfg = load_config(str(p))
    assert isinstance(cfg, AppConfig)
    assert "Politics" in cfg.compliance.allowed_categories
    assert cfg.risk.dry_run is True
    assert cfg.risk.max_total_exposure_usd == Decimal("50.0")
    assert cfg.risk.min_confidence == pytest.approx(0.6)
    assert cfg.runtime.request_timeout_s == 10


def test_load_config_defaults_on_empty_sections(tmp_path):
    p = _write_yaml(tmp_path, "{}")
    cfg = load_config(str(p))
    assert cfg.compliance.allowed_categories == []
    assert cfg.risk.dry_run is True
    assert cfg.runtime.max_requests_per_second == pytest.approx(8.0)


def test_risk_config_decimal_coercion():
    r = RiskConfig(max_total_exposure_usd=100.5)
    assert r.max_total_exposure_usd == Decimal("100.5")


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
