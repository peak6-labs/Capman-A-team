"""Market data access: markets, events, series, orderbooks.

Key responsibility for compliance: resolve a market's category. Since `category`
lives on the EVENT (not the Market), we fetch the event for a market's
`event_ticker` and cache the event_ticker -> category mapping.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .client import KalshiClient
from .models import Event, Market, Orderbook, Series


class MarketData:
    def __init__(self, client: KalshiClient) -> None:
        self.client = client
        self._event_cache: Dict[str, Event] = {}

    # ----- markets ------------------------------------------------------ #
    def list_markets(
        self,
        *,
        status: Optional[str] = None,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[List[Market], Optional[str]]:
        params: Dict[str, object] = {"limit": limit}
        if status:
            params["status"] = status
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        data = self.client.get("/markets", params=params)
        markets = [Market.model_validate(m) for m in data.get("markets", [])]
        return markets, data.get("cursor") or None

    def get_market(self, ticker: str) -> Market:
        data = self.client.get(f"/markets/{ticker}")
        return Market.model_validate(data["market"])

    def get_orderbook(self, ticker: str, depth: Optional[int] = None) -> Orderbook:
        # Per the API spec this endpoint requires auth; sign it when creds exist.
        params = {"depth": depth} if depth else None
        auth = self.client.can_authenticate()
        data = self.client.get(f"/markets/{ticker}/orderbook", params=params, auth=auth)
        return Orderbook.from_response(ticker, data)

    def get_candlesticks(
        self, series_ticker: str, ticker: str, *,
        start_ts: int, end_ts: int, period_interval: int = 1,
    ) -> List[dict]:
        """Historical OHLC + bid/ask + volume bars for a market (public, no auth).

        ``period_interval`` is in minutes (1, 60, 1440). Returns the raw candle
        dicts; prices are dollar strings under ``price``/``yes_bid``/``yes_ask``
        (``*_dollars``) and volume under ``volume_fp``. Used by the dip backtest.
        """
        data = self.client.get(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
        return data.get("candlesticks", []) or []

    # ----- events / series ---------------------------------------------- #
    def list_events(
        self,
        *,
        status: Optional[str] = None,
        series_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        with_nested_markets: bool = False,
    ) -> tuple[List[Event], Optional[str]]:
        params: Dict[str, object] = {
            "limit": limit,
            "with_nested_markets": str(with_nested_markets).lower(),
        }
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        data = self.client.get("/events", params=params)
        events = [Event.model_validate(e) for e in data.get("events", [])]
        for ev in events:
            self._event_cache[ev.event_ticker] = ev
        return events, data.get("cursor") or None

    def get_event(self, event_ticker: str) -> Event:
        if event_ticker in self._event_cache:
            return self._event_cache[event_ticker]
        data = self.client.get(f"/events/{event_ticker}")
        event = Event.model_validate(data["event"])
        self._event_cache[event_ticker] = event
        return event

    def list_series(self, *, category: Optional[str] = None) -> List[Series]:
        params = {"category": category} if category else None
        data = self.client.get("/series", params=params)
        return [Series.model_validate(s) for s in data.get("series", [])]

    # ----- compliance helper -------------------------------------------- #
    def category_for_market(self, market: Market) -> Optional[str]:
        """Resolve the category of a market via its event (cached)."""
        return self.get_event(market.event_ticker).category
