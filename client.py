"""Shared kalshi_python client, configured via config.py / environment variables.

All authentication funnels through the SDK -- `set_kalshi_auth` handles request
signing, so nothing else needs the raw private key. Import the ready-to-use API
handles (`portfolio`, `markets`, `series`) from here.

Note: this demo host returns prices in `*_dollars` string fields that the SDK's
typed models drop (e.g. Market.yes_bid is always None here). For prices, read
the raw JSON via `*_without_preload_content` -- see strategy.scan_*.
"""

from kalshi_python import Configuration, KalshiClient, MarketsApi, PortfolioApi, SeriesApi

from config import KALSHI_BASE_URL, KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_FILE


def build_client():
    configuration = Configuration(host=f"{KALSHI_BASE_URL}/trade-api/v2")
    client = KalshiClient(configuration)
    client.set_kalshi_auth(KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_FILE)
    return client


client = build_client()
portfolio = PortfolioApi(client)
markets = MarketsApi(client)
series = SeriesApi(client)
