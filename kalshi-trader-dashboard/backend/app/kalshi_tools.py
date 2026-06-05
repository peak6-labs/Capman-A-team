"""Kalshi tool schemas and dispatcher for the dashboard chat endpoint.

RESEARCH_TOOLS — read-only tools safe for the research agent.
EXECUTOR_TOOLS — adds write surface (submit/cancel) gated by compliance→risk→dry_run.

All tool handlers return a JSON string so the result can be fed back as a
Claude tool_result content block.
"""

from __future__ import annotations

import contextlib
import io
import json
from decimal import Decimal
from typing import Any, Callable, Dict, List

from kalshi_agent_trader.client import KalshiClient
from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.config import AppConfig
from kalshi_agent_trader.execution import Executor
from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.portfolio import Portfolio
from kalshi_agent_trader.risk import ProposedOrder, RiskGate
from kalshi_agent_trader.strategies.three_leg import runner as _three_leg_runner
from kalshi_agent_trader.strategies.three_leg.screen import ThreeLegParams

from .market_meta import market_meta, resolve_name, side_from_position


def _j(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _enrich_positions(positions: list[dict], client: KalshiClient) -> list[dict]:
    """Attach authoritative title/name/side from market data to each position.

    Without this the agent only sees the ticker and guesses player names from its
    suffix. `side` is derived from the signed position_fp; `name` is that side's
    sub-title. Markets that fail to resolve simply keep their original fields.
    """
    meta = market_meta(client, [p.get("ticker") for p in positions])
    enriched = []
    for p in positions:
        m = meta.get(p.get("ticker"))
        side = side_from_position(p.get("position_fp"))
        enriched.append({
            **p,
            "side": side,
            "title": m.title if m else None,
            "name": resolve_name(m, side),
        })
    return enriched


def _enrich_orders(orders: list[dict], client: KalshiClient) -> list[dict]:
    """Attach authoritative title/name to each order, keyed off its explicit side."""
    meta = market_meta(client, [o.get("ticker") for o in orders])
    enriched = []
    for o in orders:
        m = meta.get(o.get("ticker"))
        side = o.get("side")
        enriched.append({
            **o,
            "title": m.title if m else None,
            "name": resolve_name(m, str(side).lower() if side else None),
        })
    return enriched


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _list_markets(inp: dict, client: KalshiClient) -> str:
    md = MarketData(client)
    markets, _ = md.list_markets(
        status=inp.get("status", "open"),
        series_ticker=inp.get("series_ticker") or None,
        limit=min(int(inp.get("limit", 20)), 50),
    )
    return _j([
        {
            "ticker": m.ticker,
            "event": m.event_ticker,
            "status": m.status,
            "yes_bid": m.yes_bid,
            "yes_ask": m.yes_ask,
            "last_price": m.last_price,
            "liquidity": m.liquidity,
        }
        for m in markets
    ])


def _get_orderbook(inp: dict, client: KalshiClient) -> str:
    md = MarketData(client)
    ob = md.get_orderbook(inp["ticker"], depth=int(inp.get("depth", 10)))
    return _j({"ticker": inp["ticker"], "yes": ob.yes, "no": ob.no})


def _get_events(inp: dict, client: KalshiClient) -> str:
    md = MarketData(client)
    events, _ = md.list_events(
        series_ticker=inp.get("series_ticker") or None,
        limit=min(int(inp.get("limit", 20)), 50),
    )
    return _j([
        {"event_ticker": e.event_ticker, "category": e.category, "series": e.series_ticker, "title": e.title}
        for e in events
    ])


def _get_positions(inp: dict, client: KalshiClient) -> str:
    positions = Portfolio(client).market_positions()
    return _j(_enrich_positions(positions, client))


def _run_three_leg_screen(inp: dict) -> str:
    params = ThreeLegParams(
        bankroll=Decimal(str(inp.get("bankroll", 100))),
        kelly_fraction=Decimal(str(inp.get("kelly", 0.5))),
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _three_leg_runner.run(
            params,
            gender=inp.get("gender", "both"),
            players=None,
            execute=False,
            json_out=True,
        )
    return buf.getvalue().strip() or _j({"error": "no output from three-leg screen"})


def _get_account_status(inp: dict, client: KalshiClient) -> str:
    pf = Portfolio(client)
    bal = pf.balance()
    positions = pf.market_positions()
    resting = pf.resting_orders()
    return _j({
        "balance_usd": str(bal.usd()),
        "portfolio_value_usd": str(bal.portfolio_value_usd()),
        "open_positions": len(positions),
        "resting_orders": len(resting),
        "positions": _enrich_positions(positions[:20], client),
        "orders": _enrich_orders(resting, client),
    })


def _submit_order(inp: dict, client: KalshiClient, cfg: AppConfig) -> str:
    order = ProposedOrder(
        ticker=inp["ticker"],
        side=inp["side"].lower(),
        action=inp.get("action", "buy").lower(),
        price=Decimal(str(inp["price"])),
        count=int(inp["count"]),
        fair_prob=Decimal(str(inp.get("fair", 0))),
        confidence=Decimal(str(inp.get("confidence", 1.0))),
    )
    dry_run = cfg.risk.dry_run
    md = MarketData(client)
    market = md.get_market(order.ticker)
    category = md.category_for_market(market)
    account = Portfolio(client).account_state(order.ticker)
    with Journal() as journal:
        executor = Executor(
            client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
            dry_run=dry_run,
        )
        result = executor.submit(
            order, category=category, title=market.title or "", account=account, source="chat",
        )
    return _j({
        "status": result.status,
        "gate": result.gate,
        "reason": result.reason,
        "approved_count": result.approved_count,
        "dry_run": dry_run,
        "order_body": result.order_body,
    })


def _cancel_order(inp: dict, client: KalshiClient, cfg: AppConfig) -> str:
    with Journal() as journal:
        executor = Executor(
            client, ComplianceGate(cfg.compliance), RiskGate(cfg.risk), journal,
        )
        result = executor.cancel(inp["order_id"])
    return _j(result)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

ToolHandler = Callable[[Dict[str, Any], KalshiClient, AppConfig], str]

_HANDLERS: dict[str, ToolHandler] = {
    "list_markets": lambda inp, client, cfg: _list_markets(inp, client),
    "get_orderbook": lambda inp, client, cfg: _get_orderbook(inp, client),
    "get_events": lambda inp, client, cfg: _get_events(inp, client),
    "get_positions": lambda inp, client, cfg: _get_positions(inp, client),
    "run_three_leg_screen": lambda inp, client, cfg: _run_three_leg_screen(inp),
    "get_account_status": lambda inp, client, cfg: _get_account_status(inp, client),
    "submit_order": _submit_order,
    "cancel_order": _cancel_order,
}


def _tool_schema(name: str) -> dict | None:
    for tool in EXECUTOR_TOOLS:
        if tool.get("name") == name:
            return tool.get("input_schema")
    return None


def _validate_tool_input(name: str, inp: Dict[str, Any]) -> str | None:
    schema = _tool_schema(name)
    if schema is None:
        return f"unknown tool: {name}"
    missing = [field for field in schema.get("required", []) if field not in inp]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    return None


def execute_tool(name: str, inp: Dict[str, Any], client: KalshiClient, cfg: AppConfig) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return _j({"error": f"unknown tool: {name}"})
    validation_error = _validate_tool_input(name, inp)
    if validation_error:
        return _j({"error": validation_error})
    try:
        return handler(inp, client, cfg)
    except Exception as exc:
        return _j({"error": str(exc)})


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

RESEARCH_TOOLS: List[dict] = [
    {
        "name": "get_account_status",
        "description": "Get account balance, portfolio value, open market positions, AND resting (open/unfilled) orders. Call this for a complete portfolio snapshot — get_positions alone does not include resting orders.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_markets",
        "description": "List Kalshi markets. Returns ticker, bid/ask, last price, liquidity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "closed", "settled"], "default": "open"},
                "series_ticker": {"type": "string", "description": "Filter by series ticker (e.g. KXFRENOPEN)"},
                "limit": {"type": "integer", "default": 20, "maximum": 50},
            },
        },
    },
    {
        "name": "get_orderbook",
        "description": "Fetch the live orderbook for a market ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Market ticker (e.g. KXFRENOPEN-25-SINNER)"},
                "depth": {"type": "integer", "default": 10, "description": "Levels per side"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_events",
        "description": "List Kalshi events with their category and series.",
        "input_schema": {
            "type": "object",
            "properties": {
                "series_ticker": {"type": "string", "description": "Filter by series ticker"},
                "limit": {"type": "integer", "default": 20, "maximum": 50},
            },
        },
    },
    {
        "name": "get_positions",
        "description": "Get current open market positions for the account (requires auth).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_three_leg_screen",
        "description": (
            "Run the three-leg fatigue-hedge screen for tennis (e.g. French Open QF favourites). "
            "Returns a JSON snapshot with Kelly-sized legs, de-vigged fairs, and EV per player. "
            "Screen-only — never places orders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gender": {"type": "string", "enum": ["men", "women", "both"], "default": "both"},
                "bankroll": {"type": "number", "default": 100, "description": "Bankroll for Kelly sizing"},
                "kelly": {"type": "number", "default": 0.5, "description": "Kelly fraction (0.5 = half-Kelly)"},
            },
        },
    },
]

EXECUTOR_TOOLS: List[dict] = RESEARCH_TOOLS + [
    {
        "name": "submit_order",
        "description": (
            "Submit an order through compliance → risk → execution. "
            "Dry-run is the default (config risk.dry_run); pass live via config only. "
            "Returns status: placed | dry_run | rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Market ticker"},
                "side": {"type": "string", "enum": ["yes", "no"]},
                "action": {"type": "string", "enum": ["buy", "sell"], "default": "buy"},
                "price": {"type": "number", "description": "Limit price in dollars (0..1)"},
                "count": {"type": "integer", "description": "Number of contracts"},
                "fair": {"type": "number", "default": 0, "description": "Your fair probability (0..1)"},
                "confidence": {"type": "number", "default": 1.0, "description": "Confidence (0..1)"},
            },
            "required": ["ticker", "side", "price", "count"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel a resting order by its Kalshi order ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Kalshi order ID to cancel"},
            },
            "required": ["order_id"],
        },
    },
]
