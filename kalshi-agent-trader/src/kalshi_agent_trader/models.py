"""Pydantic models for Kalshi API objects.

IMPORTANT (verified against the live API, June 2026):
  - Prices are DOLLAR STRINGS, not integer cents (e.g. "0.9930"). We parse to Decimal.
  - `category` lives on the EVENT, not the Market. Markets carry `event_ticker`;
    resolve market -> event -> category for compliance.
  - Orderbook is wrapped as {"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}},
    each level being [price_dollars, size].

Models use extra="ignore" so the app is resilient to additional/renamed API fields.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_decimal(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    return Decimal(str(value))


class Market(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    ticker: str
    event_ticker: str
    market_type: Optional[str] = None
    status: Optional[str] = None
    title: Optional[str] = None
    expiration_time: Optional[str] = None
    expected_expiration_time: Optional[str] = None
    mve_collection_ticker: Optional[str] = None

    # Per-player identity for structured markets (tennis, etc.). `yes_sub_title`
    # carries the player's display name; `custom_strike.tennis_competitor` is a
    # stable competitor UUID shared across that player's markets.
    yes_sub_title: Optional[str] = None
    no_sub_title: Optional[str] = None
    custom_strike: Optional[dict] = None
    strike_type: Optional[str] = None

    volume_fp: Optional[float] = None

    yes_bid: Optional[Decimal] = Field(default=None, alias="yes_bid_dollars")
    yes_ask: Optional[Decimal] = Field(default=None, alias="yes_ask_dollars")
    no_bid: Optional[Decimal] = Field(default=None, alias="no_bid_dollars")
    no_ask: Optional[Decimal] = Field(default=None, alias="no_ask_dollars")
    last_price: Optional[Decimal] = Field(default=None, alias="last_price_dollars")
    liquidity: Optional[Decimal] = Field(default=None, alias="liquidity_dollars")
    notional_value: Optional[Decimal] = Field(default=None, alias="notional_value_dollars")

    @field_validator(
        "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "liquidity",
        "notional_value", mode="before",
    )
    @classmethod
    def _parse_money(cls, v):
        return _to_decimal(v)


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_ticker: str
    series_ticker: Optional[str] = None
    category: Optional[str] = None
    title: Optional[str] = None
    sub_title: Optional[str] = None
    mutually_exclusive: Optional[bool] = None


class Series(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    category: Optional[str] = None
    title: Optional[str] = None
    frequency: Optional[str] = None
    tags: Optional[List[str]] = None
    additional_prohibitions: Optional[List[str]] = None


class Orderbook(BaseModel):
    """Parsed orderbook. `yes`/`no` are lists of (price, size) as Decimals.

    Sizes are Decimal (not int) because Kalshi supports fractional trading — the
    API returns sizes as strings like "21.00".
    """

    model_config = ConfigDict(extra="ignore")

    ticker: str
    yes: List[Tuple[Decimal, Decimal]] = Field(default_factory=list)
    no: List[Tuple[Decimal, Decimal]] = Field(default_factory=list)

    @classmethod
    def from_response(cls, ticker: str, payload: dict) -> "Orderbook":
        """Build from the raw {"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}}.

        Each level is [price_dollars, size] with both as strings, e.g. ["0.5340", "21.00"].
        """
        fp = (payload or {}).get("orderbook_fp", payload or {})

        def parse(levels) -> List[Tuple[Decimal, Decimal]]:
            out: List[Tuple[Decimal, Decimal]] = []
            for level in levels or []:
                if isinstance(level, (list, tuple)) and len(level) >= 2:
                    out.append((Decimal(str(level[0])), Decimal(str(level[1]))))
            return out

        return cls(
            ticker=ticker,
            yes=parse(fp.get("yes_dollars") or fp.get("yes")),
            no=parse(fp.get("no_dollars") or fp.get("no")),
        )


class Balance(BaseModel):
    """Account balance. Field names are confirmed empirically once a key exists;
    we capture common variants and expose dollars."""

    model_config = ConfigDict(extra="allow")

    balance_dollars: Optional[Decimal] = None
    balance: Optional[Decimal] = None          # integer cents
    portfolio_value: Optional[Decimal] = None  # integer cents

    def usd(self) -> Optional[Decimal]:
        if self.balance_dollars is not None:
            return Decimal(str(self.balance_dollars))
        if self.balance is not None:
            return Decimal(str(self.balance)) / Decimal("100")
        return None

    def portfolio_value_usd(self) -> Optional[Decimal]:
        if self.portfolio_value is None:
            return None
        return Decimal(str(self.portfolio_value)) / Decimal("100")
