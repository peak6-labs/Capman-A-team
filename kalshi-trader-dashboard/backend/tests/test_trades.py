from app.market_meta import MarketMeta
from app.routers import trades as trades_router
from app.serializers import normalize_fill


def test_normalize_fill_preserves_historical_fill_fields():
    row = normalize_fill(
        {
            "created_time": "2026-06-05T12:00:00Z",
            "ticker": "TEST-MARKET",
            "side": "yes",
            "action": "buy",
            "count_fp": "3.00",
            "yes_price_dollars": "0.42",
            "no_price_dollars": "0.58",
            "fee_cost": "0.03",
            "is_taker": True,
            "trade_id": "TRADE-1",
        },
        MarketMeta(
            title="Will the test market settle yes?",
            yes_sub_title="YES contract",
            no_sub_title="NO contract",
        ),
    )

    assert row is not None
    assert row["ticker"] == "TEST-MARKET"
    assert row["count"] == "3.00"
    assert row["price_usd"] == "0.42"
    assert row["yes_price_usd"] == "0.42"
    assert row["no_price_usd"] == "0.58"
    assert row["fee_usd"] == "0.03"
    assert row["is_taker"] is True
    assert row["fill_id"] == "TRADE-1"
    assert row["title"] == "Will the test market settle yes?"
    assert row["name"] == "YES contract"


def test_trade_history_merges_historical_fills_and_enriches_decisions(monkeypatch):
    class FakePortfolio:
        def __init__(self, _client):
            pass

        def fills(self):
            return [
                {
                    "created_time": "2026-06-05T12:00:00Z",
                    "ticker": "LIVE-MARKET",
                    "side": "yes",
                    "action": "buy",
                    "count": "1",
                    "price_dollars": "0.25",
                    "fill_id": "LIVE-1",
                }
            ]

        def historical_fills(self):
            return [
                {
                    "created_time": "2026-06-04T12:00:00Z",
                    "ticker": "HIST-MARKET",
                    "side": "no",
                    "action": "buy",
                    "count_fp": "2.00",
                    "yes_price_dollars": "0.80",
                    "no_price_dollars": "0.20",
                    "fee_cost": "0.01",
                    "is_taker": False,
                    "trade_id": "HIST-1",
                }
            ]

    class FakeJournal:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def recent_decisions(self, limit):
            return [
                {
                    "ts": 1780603200000,
                    "source": "agent",
                    "market_ticker": "DECISION-MARKET",
                    "side": "yes",
                    "target_price": "0.44",
                    "fair_prob": 0.61,
                    "confidence": 0.72,
                    "max_contracts": "5",
                    "outcome": "placed",
                    "gate": None,
                    "reason": "edge cleared",
                }
            ][:limit]

    def fake_meta(_client, tickers):
        del tickers
        return {
            "LIVE-MARKET": MarketMeta(title="Live market", yes_sub_title="Live yes"),
            "HIST-MARKET": MarketMeta(title="Historical market", no_sub_title="Historical no"),
            "DECISION-MARKET": MarketMeta(title="Decision market", yes_sub_title="Decision yes"),
        }

    monkeypatch.setattr(trades_router, "get_client", lambda: object())
    monkeypatch.setattr(trades_router, "Portfolio", FakePortfolio)
    monkeypatch.setattr(trades_router, "Journal", FakeJournal)
    monkeypatch.setattr(trades_router, "market_meta", fake_meta)

    result = trades_router.trade_history(limit=10)

    assert [fill["fill_id"] for fill in result["fills"]] == ["LIVE-1", "HIST-1"]
    assert result["fills"][1]["count"] == "2.00"
    assert result["fills"][1]["no_price_usd"] == "0.20"
    assert result["fills"][1]["fee_usd"] == "0.01"
    assert result["fills"][1]["name"] == "Historical no"
    assert result["decisions"] == [
        {
            "ts": 1780603200000,
            "source": "agent",
            "market_ticker": "DECISION-MARKET",
            "side": "yes",
            "target_price": "0.44",
            "fair_prob": 0.61,
            "confidence": 0.72,
            "max_contracts": "5",
            "outcome": "placed",
            "gate": None,
            "reason": "edge cleared",
            "title": "Decision market",
            "name": "Decision yes",
        }
    ]


def test_trade_history_keeps_live_fills_when_historical_endpoint_fails(monkeypatch):
    class FakePortfolio:
        def __init__(self, _client):
            pass

        def fills(self):
            return [
                {
                    "created_time": "2026-06-05T12:00:00Z",
                    "ticker": "LIVE-MARKET",
                    "count": "1",
                    "price_dollars": "0.25",
                    "fill_id": "LIVE-1",
                }
            ]

        def historical_fills(self):
            raise RuntimeError("endpoint unavailable")

    class FakeJournal:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def recent_decisions(self, limit):
            del limit
            return []

    monkeypatch.setattr(trades_router, "get_client", lambda: object())
    monkeypatch.setattr(trades_router, "Portfolio", FakePortfolio)
    monkeypatch.setattr(trades_router, "Journal", FakeJournal)
    monkeypatch.setattr(trades_router, "market_meta", lambda _client, _tickers: {})

    result = trades_router.trade_history(limit=10)

    assert [fill["fill_id"] for fill in result["fills"]] == ["LIVE-1"]
    assert result["decisions"] == []
