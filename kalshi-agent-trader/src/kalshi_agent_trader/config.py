"""Application configuration: secrets (from .env), compliance, risk, and runtime settings.

Secrets are loaded via pydantic-settings from environment variables / .env file.
ComplianceConfig, RiskConfig, and RuntimeConfig are loaded from config.yaml by load_config().
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

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


class RelativeValueConfig(BaseModel):
    """External-reference signal settings for Kalshi-only relative-value trading."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    min_edge: Decimal = Decimal("0.025")
    min_match_confidence: float = 0.75
    max_signal_age_s: int = 30
    max_spread: Decimal = Decimal("0.20")
    max_markets: int = 200
    max_signals: int = 10
    order_count: int = 1
    allowed_sources: List[str] = Field(default_factory=lambda: ["polymarket"])

    @field_validator("min_edge", "max_spread", mode="before")
    @classmethod
    def _to_decimal(cls, v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v))


class SportsbookTargetConfig(BaseModel):
    """A specific sportsbook page to scrape for a specific Kalshi market."""

    model_config = ConfigDict(extra="ignore")

    source: str
    url: str
    outcome: str
    side: str = "yes"

    @field_validator("side", mode="before")
    @classmethod
    def _side_to_string(cls, v):
        if isinstance(v, bool):
            return "yes" if v else "no"
        return str(v).lower()


class SportsbookScrapeConfig(BaseModel):
    """Targeted sportsbook scraping, only for already-proposed Kalshi trades."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    require_quote_for_agent: bool = False
    timeout_s: int = 10
    max_response_bytes: int = 1_000_000
    min_parse_confidence: float = 0.55
    max_reference_disagreement: Optional[float] = 0.25
    blend_weight: float = 0.50
    user_agent: str = "Mozilla/5.0 (compatible; kalshi-agent-trader/0.1; targeted odds check)"
    market_urls: Dict[str, List[SportsbookTargetConfig]] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    """Claude model IDs for the agent tiers.

    The scout does breadth (cheap triage); the analyst does depth (high-stakes pricing).
    AnalystAgent enforces a Sonnet model; ScoutAgent rejects Sonnet models.
    """

    model_config = ConfigDict(extra="ignore")

    scout_model: str = "claude-haiku-4-5-20251001"
    analyst_model: str = "claude-sonnet-4-6"


class AppConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    secrets: SecretsConfig
    compliance: ComplianceConfig
    risk: RiskConfig
    runtime: RuntimeConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    relative_value: RelativeValueConfig = Field(default_factory=RelativeValueConfig)
    sportsbook_scrape: SportsbookScrapeConfig = Field(default_factory=SportsbookScrapeConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)


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
        relative_value=RelativeValueConfig(**data.get("relative_value", {})),
        sportsbook_scrape=SportsbookScrapeConfig(**data.get("sportsbook_scrape", {})),
        models=ModelsConfig(**data.get("models", {})),
    )
