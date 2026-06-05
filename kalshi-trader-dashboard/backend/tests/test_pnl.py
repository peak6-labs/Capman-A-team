from datetime import date, datetime
from decimal import Decimal

from app.routers import pnl as pnl_router
from app.pnl import daily_pnl_series, daily_settlement_pnl_series, settlement_history_rows, trade_pnl_rows


def ts(dt: datetime) -> int:
    return int(dt.timestamp())


class FakePortfolio:
    def __init__(self, _client):
        pass

    def market_positions(self):
        return []

    def fills(self):
        return []

    def settlements(self):
        return [
            {
                "ticker": "MARKET-D",
                "market_result": "no",
                "yes_count_fp": "1.00",
                "yes_total_cost_dollars": "0.99",
                "settled_time": "2026-06-02T18:00:00Z",
            }
        ]


def test_pnl_daily_fetches_settlements(monkeypatch):
    monkeypatch.setattr(pnl_router, "get_client", lambda: object())
    monkeypatch.setattr(pnl_router, "Portfolio", FakePortfolio)

    result = pnl_router.pnl_daily(start="2026-06-01")

    assert result["points"][1] == {
        "date": "2026-06-02",
        "realized_usd": "-0.99",
        "cumulative_realized_usd": "-0.99",
    }


def test_daily_pnl_series_uses_cashflow_without_erasing_losses():
    fills = [
        {
            "ts": ts(datetime(2026, 5, 31, 12)),
            "ticker": "MARKET-A",
            "side": "yes",
            "action": "buy",
            "count": "10",
            "price_dollars": "0.10",
        },
        {
            "ts": ts(datetime(2026, 6, 1, 12)),
            "ticker": "MARKET-A",
            "side": "yes",
            "action": "sell",
            "count": "5",
            "price_dollars": "0.20",
        },
        {
            "ts": ts(datetime(2026, 6, 2, 12)),
            "ticker": "MARKET-A",
            "side": "yes",
            "action": "buy",
            "count": "2",
            "price_dollars": "0.50",
        },
    ]

    points = daily_pnl_series(
        fills,
        current_realized=None,
        start=date(2026, 6, 1),
        end=date(2026, 6, 2),
    )

    assert points == [
        {
            "date": "2026-06-01",
            "realized_usd": "-4",
            "cumulative_realized_usd": "-5",
        },
        {
            "date": "2026-06-02",
            "realized_usd": "-1",
            "cumulative_realized_usd": "-6",
        },
    ]


def test_trade_pnl_rows_show_yes_position_cost_and_position_pnl():
    fills = [
        {
            "ts": ts(datetime(2026, 6, 1, 9)),
            "ticker": "MARKET-A",
            "side": "yes",
            "action": "buy",
            "count": "10",
            "price_dollars": "0.40",
            "fill_id": "fill-1",
        },
    ]
    positions = [
        {
            "ticker": "MARKET-A",
            "position_fp": "10",
            "realized_pnl_dollars": "0.80",
            "unrealized_pnl_dollars": "-0.10",
        }
    ]

    rows = trade_pnl_rows(fills, positions)

    assert len(rows) == 1
    assert rows[0]["ticker"] == "MARKET-A"
    assert rows[0]["entry_price_usd"] == "0.4"
    assert rows[0]["held_side"] == "yes"
    assert rows[0]["final_position"] == "10 Yes"
    assert rows[0]["entry_count"] == "10"
    assert rows[0]["open_count"] == "10"
    assert rows[0]["total_cost_usd"] == "4"
    assert rows[0]["total_payout_usd"] == "4.7"
    assert rows[0]["total_return_usd"] == "0.7"
    assert rows[0]["pnl_usd"] == "0.7"
    assert rows[0]["status"] == "open"
    assert rows[0]["orders"] == [
        {
            "ts": ts(datetime(2026, 6, 1, 9)) * 1000,
            "action": "buy",
            "side": "yes",
            "count": "10",
            "price_usd": "0.4",
            "fee_usd": "0",
            "fill_id": "fill-1",
        },
    ]


def test_trade_pnl_rows_treat_sell_yes_as_no_position_cost():
    fills = [
        {
            "ts": ts(datetime(2026, 6, 1, 14)),
            "ticker": "MARKET-A",
            "side": "yes",
            "action": "sell",
            "count": "4",
            "price_dollars": "0.70",
            "fill_id": "fill-2",
        },
    ]

    rows = trade_pnl_rows(fills, positions=[])

    assert len(rows) == 1
    assert rows[0]["held_side"] == "no"
    assert rows[0]["final_position"] == "4 No"
    assert rows[0]["entry_price_usd"] == "0.3"
    assert rows[0]["total_cost_usd"] == "1.2"
    assert rows[0]["pnl_usd"] == "-1.2"
    assert rows[0]["orders"][0]["side"] == "no"
    assert rows[0]["orders"][0]["price_usd"] == "0.3"


def test_trade_pnl_rows_handle_kalshi_fill_count_price_and_closed_loss():
    fills = [
        {
            "ts": ts(datetime(2026, 6, 5, 8, 34)),
            "ticker": "MARKET-B",
            "side": "yes",
            "action": "buy",
            "fill_count": "10.00",
            "average_fill_price": "0.4200",
            "average_fee_paid": "0.0100",
            "fill_id": "fill-3",
        },
    ]

    rows = trade_pnl_rows(fills, positions=[])

    assert len(rows) == 1
    assert rows[0]["entry_price_usd"] == "0.42"
    assert rows[0]["entry_count"] == "10"
    assert rows[0]["open_count"] == "0"
    assert rows[0]["status"] == "closed"
    assert rows[0]["pnl_usd"] == "-4.3"
    assert rows[0]["orders"][0]["price_usd"] == "0.42"
    assert rows[0]["orders"][0]["count"] == "10"
    assert rows[0]["orders"][0]["fee_usd"] == "0.1"


def test_settlement_history_rows_match_kalshi_history_math():
    rows = settlement_history_rows([
        {
            "ticker": "MARKET-C",
            "event_ticker": "EVENT-C",
            "market_result": "no",
            "yes_count_fp": "1.00",
            "yes_total_cost_dollars": "0.99",
            "no_count_fp": "2.00",
            "no_total_cost_dollars": "1.86",
            "revenue": 200,
            "fee_cost": "0",
            "settled_time": "2026-06-02T18:00:00Z",
        },
    ])

    assert len(rows) == 2
    yes_row = next(row for row in rows if row["held_side"] == "yes")
    no_row = next(row for row in rows if row["held_side"] == "no")

    assert yes_row["final_position"] == "1 Yes"
    assert yes_row["settlement_payout_usd"] == "0"
    assert yes_row["total_cost_usd"] == "0.99"
    assert yes_row["total_return_usd"] == "-0.99"
    assert yes_row["total_return_pct"] == "-100"

    assert no_row["final_position"] == "2 No"
    assert no_row["settlement_payout_usd"] == "2"
    assert no_row["total_cost_usd"] == "1.86"
    assert no_row["total_return_usd"] == "0.14"
    assert no_row["total_return_pct"] == "7.5269"


def test_daily_settlement_pnl_series_groups_returns_by_settlement_day():
    points = daily_settlement_pnl_series(
        [
            {
                "ticker": "MARKET-D",
                "market_result": "no",
                "yes_count_fp": "1.00",
                "yes_total_cost_dollars": "0.99",
                "settled_time": "2026-06-02T18:00:00Z",
            },
            {
                "ticker": "MARKET-E",
                "market_result": "yes",
                "yes_count_fp": "2.00",
                "yes_total_cost_dollars": "1.86",
                "settled_time": "2026-06-03T18:00:00Z",
            },
        ],
        start=date(2026, 6, 1),
        end=date(2026, 6, 3),
    )

    assert points == [
        {"date": "2026-06-01", "realized_usd": "0", "cumulative_realized_usd": "0"},
        {"date": "2026-06-02", "realized_usd": "-0.99", "cumulative_realized_usd": "-0.99"},
        {"date": "2026-06-03", "realized_usd": "0.14", "cumulative_realized_usd": "-0.85"},
    ]
