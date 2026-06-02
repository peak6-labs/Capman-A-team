"""Kalshi-only relative-value pipeline driven by external reference prices."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Dict, Iterable, List, Optional

from kalshi_agent_trader.client import KalshiClient
from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.config import AppConfig
from kalshi_agent_trader.execution import Executor
from kalshi_agent_trader.journal import Journal
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.models import Market
from kalshi_agent_trader.polymarket import PolymarketClient
from kalshi_agent_trader.portfolio import Portfolio
from kalshi_agent_trader.risk import RiskGate

from .engine import RelativeValueEngine
from .models import ReferenceQuote, RelativeValueSignal
from .sources import PolymarketReferenceSource, ReferenceSource


def _iter_open_markets(md: MarketData, *, max_markets: int) -> Iterable[Market]:
    cursor: Optional[str] = None
    yielded = 0
    while yielded < max_markets:
        limit = min(200, max_markets - yielded)
        markets, cursor = md.list_markets(status="open", limit=limit, cursor=cursor)
        if not markets:
            return
        for market in markets:
            yielded += 1
            yield market
            if yielded >= max_markets:
                return
        if not cursor:
            return


def _journal_quote(journal: Journal, quote: ReferenceQuote, market: Market) -> None:
    payload = asdict(quote)
    payload["market_ticker"] = market.ticker
    journal.record_reference_quote(payload)


def _journal_signal(
    journal: Journal,
    signal: RelativeValueSignal,
    *,
    outcome: str,
    reason: Optional[str] = None,
) -> None:
    journal.record_relative_value_signal(
        {
            "source": signal.source,
            "market_ticker": signal.ticker,
            "title": signal.title,
            "side": signal.side,
            "action": signal.action,
            "kalshi_price": signal.kalshi_price,
            "reference_prob": signal.reference_prob,
            "edge": signal.edge,
            "confidence": signal.confidence,
            "outcome": outcome,
            "reason": reason or signal.reason,
        }
    )


def collect_signals(
    config: AppConfig,
    *,
    journal: Optional[Journal] = None,
) -> List[RelativeValueSignal]:
    """Find relative-value signals without placing orders."""
    rv = config.relative_value
    if not rv.enabled:
        return []

    signals: List[RelativeValueSignal] = []
    with KalshiClient(config) as client, PolymarketClient(
        timeout=config.runtime.request_timeout_s,
        verify_ssl=config.runtime.verify_ssl,
    ) as poly:
        md = MarketData(client)
        compliance = ComplianceGate(config.compliance)
        engine = RelativeValueEngine(
            min_edge=rv.min_edge,
            min_confidence=rv.min_match_confidence,
            max_spread=rv.max_spread,
        )
        sources: List[ReferenceSource] = []
        allowed_sources = {s.lower() for s in rv.allowed_sources}
        if "polymarket" in allowed_sources:
            sources.append(PolymarketReferenceSource(poly))

        for market in _iter_open_markets(md, max_markets=rv.max_markets):
            comp = compliance.check_market(market, md)
            if not comp.allowed:
                continue

            for source in sources:
                quote = source.fetch_for_market(market)
                if quote is None:
                    continue
                if time.time() - quote.ts > rv.max_signal_age_s:
                    continue
                if journal is not None:
                    _journal_quote(journal, quote, market)
                found = engine.signals_for_market(
                    market,
                    quote,
                    category=comp.category,
                )
                for signal in found:
                    if journal is not None:
                        _journal_signal(journal, signal, outcome="found")
                signals.extend(found)

    signals.sort(key=lambda s: (s.edge, s.confidence), reverse=True)
    return signals[: rv.max_signals]


def run(config: AppConfig, *, live: bool = False, dry_run: Optional[bool] = None) -> Dict[str, int]:
    """Collect signals and submit Kalshi-only proposals through the gate chain."""
    rv = config.relative_value
    dry_run = config.risk.dry_run if dry_run is None else dry_run
    if live:
        dry_run = False

    with Journal() as journal:
        signals = collect_signals(config, journal=journal)
        counts: Dict[str, int] = {
            "signals": len(signals),
            "placed": 0,
            "dry_run": 0,
            "rejected": 0,
        }
        if not signals:
            return counts

        with KalshiClient(config) as client:
            compliance = ComplianceGate(config.compliance)
            risk = RiskGate(config.risk)
            portfolio = Portfolio(client)
            executor = Executor(client, compliance, risk, journal, dry_run=dry_run)
            engine = RelativeValueEngine(
                min_edge=rv.min_edge,
                min_confidence=rv.min_match_confidence,
                max_spread=rv.max_spread,
            )

            for signal in signals:
                order = engine.to_order(signal, count=rv.order_count)
                try:
                    account = portfolio.account_state(signal.ticker)
                except Exception as exc:
                    counts["rejected"] += 1
                    _journal_signal(
                        journal,
                        signal,
                        outcome="rejected",
                        reason=f"account state unavailable: {exc}",
                    )
                    continue

                result = executor.submit(
                    order,
                    category=signal.category,
                    title=signal.title,
                    account=account,
                    source=f"relative_value:{signal.source}",
                )
                counts[result.status] += 1
                _journal_signal(
                    journal,
                    signal,
                    outcome=result.status,
                    reason=result.reason or signal.reason,
                )

    return counts
