"""Tests for portfolio-derived risk state."""

from decimal import Decimal

from kalshi_agent_trader.portfolio import Portfolio


class FakeClient:
    def get(self, path, params=None, auth=False):
        if path == "/portfolio/balance":
            return {"balance_dollars": "100.00"}
        if path == "/portfolio/positions":
            return {"market_positions": []}
        if path == "/portfolio/orders":
            return {
                "orders": [
                    {
                        "ticker": "T-1",
                        "action": "sell",
                        "price_dollars": "0.05",
                        "count": "10",
                    }
                ]
            }
        raise AssertionError(path)


def test_account_state_resting_sell_uses_max_loss_exposure():
    state = Portfolio(FakeClient()).account_state("T-1")
    assert state.total_exposure_usd == Decimal("9.50")
    assert state.position_exposure_usd == Decimal("9.50")


def test_historical_fills_uses_historical_endpoint():
    class HistoricalClient(FakeClient):
        def get(self, path, params=None, auth=False):
            if path == "/historical/fills":
                return {"fills": [{"fill_id": "F-1"}]}
            return super().get(path, params=params, auth=auth)

    assert Portfolio(HistoricalClient()).historical_fills() == [{"fill_id": "F-1"}]
