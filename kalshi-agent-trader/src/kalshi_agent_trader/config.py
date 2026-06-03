"""Application configuration: secrets (from .env), compliance, risk, and runtime settings.

Secrets are loaded via pydantic-settings from environment variables / .env file.
ComplianceConfig, RiskConfig, and RuntimeConfig are loaded from config.yaml by load_config().
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = kalshi-agent-trader/ (two levels above this package file).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SecretsConfig(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    kalshi_api_key_id: Optional[str] = None
    kalshi_private_key_path: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    kalshi_api_base: str = "https://external-api.kalshi.com/trade-api/v2"

    def require_kalshi(self) -> None:
        if not self.kalshi_api_key_id:
            raise RuntimeError(
                "KALSHI_API_KEY_ID not set — add it to .env or export as an env var."
            )
        if not self.kalshi_private_key_path:
            raise RuntimeError(
                "KALSHI_PRIVATE_KEY_PATH not set — add it to .env or export as an env var."
            )


class ComplianceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    allowed_categories: List[str] = []
    prohibited_categories: List[str] = []
    default_deny_unknown: bool = True
    prohibited_keywords: List[str] = []


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dry_run: bool = True
    max_total_exposure_usd: Decimal = Decimal("0")
    max_per_position_usd: Decimal = Decimal("0")
    max_contracts_per_order: int = 0
    daily_loss_cap_usd: Decimal = Decimal("0")
    min_confidence: float = 0.0
    min_edge: float = 0.0

    @field_validator(
        "max_total_exposure_usd",
        "max_per_position_usd",
        "daily_loss_cap_usd",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v))


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_timeout_s: int = 15
    max_requests_per_second: float = 8.0
    verify_ssl: bool = True


class StrategyConfig(BaseModel):
    """Probability/sizing parameters for the systematic brain and exit monitor.

    These are strategy tunables (distinct from RiskConfig's hard USD caps).
    Defaults match the conservative starting values; override in config.yaml's
    `strategy:` section. Calibrate the discount-driven sizing against historical
    Kalshi resolution data before widening.
    """

    model_config = ConfigDict(extra="ignore")

    # Sizing (brain.py)
    bankroll_usd: float = 100.0
    max_kelly: float = 0.25                # quarter-Kelly cap
    max_risk_per_position: float = 0.01    # hard cap: 1% of bankroll per position
    min_confidence: float = 0.60
    max_theses: int = 10

    # Exit triggers (monitor.py)
    target_fraction: float = 0.15          # exit when bid ≤ this fraction of entry
    near_expiry_hours: float = 2.0
    stale_hours: float = 24.0
    stale_move_pct: float = 0.02


class ThreeLegConfig(BaseModel):
    """Sizing + execution parameters for the three-leg fatigue-hedge strategy.

    The canonical defaults the research agent screens with (`three-leg --json`)
    live here, plus the executor's trade-ticket guards: a ticket older than
    `ticket_max_age_min`, or whose match/title ask has drifted past the matching
    `drift_tolerance_*`, is refused. These bands are copied into each ticket's
    frontmatter so the executor's check is self-contained.
    """

    model_config = ConfigDict(extra="ignore")

    bankroll_usd: Decimal = Decimal("100")
    kelly_fraction: Decimal = Decimal("0.5")
    fatigue_coef: Decimal = Decimal("0.20")
    fee_rate: Decimal = Decimal("0.07")
    rest_days: int = 1
    # Directional alpha (added to the de-vigged market fair). Legs size to 0 at
    # market, so a positive edge here is what makes the strategy actually trade.
    match_edge: Decimal = Decimal("0")
    title_edge: Decimal = Decimal("0")
    # Executor staleness / drift guards (the trade-ticket contract).
    ticket_max_age_min: int = 30
    drift_tolerance_match: Decimal = Decimal("0.02")
    drift_tolerance_title: Decimal = Decimal("0.03")

    @field_validator(
        "bankroll_usd", "kelly_fraction", "fatigue_coef", "fee_rate",
        "match_edge", "title_edge",
        "drift_tolerance_match", "drift_tolerance_title", mode="before",
    )
    @classmethod
    def _to_decimal(cls, v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v))


class AppConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    secrets: SecretsConfig
    compliance: ComplianceConfig
    risk: RiskConfig
    runtime: RuntimeConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    three_leg: ThreeLegConfig = Field(default_factory=ThreeLegConfig)


def load_config(yaml_path: Optional[str] = None) -> AppConfig:
    """Load AppConfig from config.yaml + .env / environment variables."""
    path = Path(yaml_path) if yaml_path else _PROJECT_ROOT / "config.yaml"
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    env_file = _PROJECT_ROOT / ".env"
    secrets = SecretsConfig(_env_file=str(env_file) if env_file.exists() else None)

    return AppConfig(
        secrets=secrets,
        compliance=ComplianceConfig(**data.get("compliance", {})),
        risk=RiskConfig(**data.get("risk", {})),
        runtime=RuntimeConfig(**data.get("runtime", {})),
        strategy=StrategyConfig(**data.get("strategy", {})),
        three_leg=ThreeLegConfig(**data.get("three_leg", {})),
    )
