"""Application configuration: secrets (from .env), compliance, risk, and runtime settings.

Secrets are loaded via pydantic-settings from environment variables / .env file.
ComplianceConfig, RiskConfig, and RuntimeConfig are loaded from config.yaml by load_config().
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
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


class AppConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    secrets: SecretsConfig
    compliance: ComplianceConfig
    risk: RiskConfig
    runtime: RuntimeConfig


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
    )
