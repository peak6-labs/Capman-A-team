"""Agent-enhanced pipeline: Claude scans events, then evaluates survivors.

Pipeline:
  1. List open events filtered to compliance-allowed categories (reduces Claude input size).
  2. Agent finds opportunities in the event list (find_opportunities).
  3. For each signal: fetch the Kalshi market and run compliance + scanner filters.
  4. Agent evaluates each survivor with Polymarket reference (evaluate).
  5. Build ProposedOrder from the refined Signal and submit through the gate chain.

Agents PROPOSE. Compliance → risk → execution DISPOSE.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional

from ..brain import MAX_THESES
from ..client import KalshiClient
from ..compliance import ComplianceGate
from ..config import AppConfig
from ..execution import Executor
from ..journal import Journal
from ..market_data import MarketData
from ..polymarket import PolymarketClient
from ..portfolio import Portfolio
from ..risk import ProposedOrder, RiskGate
from ..scanner import MAX_HOURS, MAX_SPREAD, MIN_HOURS, MIN_PRICE, MIN_VOLUME_FP, ScanCandidate
from ..util import hours_until, volume_fp
from .base import Signal
from .market_agent import MarketAgent


def _signal_to_proposed(signal: Signal, market_price: Decimal) -> ProposedOrder:
    return ProposedOrder(
        ticker=signal.ticker,
        side=signal.side,
        price=market_price,
        count=1,            # risk gate will clamp to fit caps
        fair_prob=signal.fair_prob,
        confidence=signal.confidence,
    )


def run_agent_strategy(
    config: AppConfig,
    *,
    live: bool = False,
    max_events: int = 50,
) -> Dict[str, int]:
    """Run one agent-enhanced scan → evaluate → execute cycle."""
    dry_run = config.risk.dry_run and not live
    allowed_cats = {c.lower() for c in config.compliance.allowed_categories}

    with (
        KalshiClient(config) as client,
        Journal() as journal,
        PolymarketClient(timeout=config.runtime.request_timeout_s, verify_ssl=config.runtime.verify_ssl) as poly,
    ):
        md = MarketData(client)
        compliance = ComplianceGate(config.compliance)
        risk = RiskGate(config.risk)
        portfolio = Portfolio(client)
        executor = Executor(client, compliance, risk, journal, dry_run=dry_run)
        agent = MarketAgent(api_key=config.secrets.anthropic_api_key or "")

        # Step 1: fetch open events, pre-filter to allowed categories.
        allowed_events = []
        cursor: Optional[str] = None
        fetched = 0
        while fetched < max_events:
            batch_limit = min(100, max_events - fetched)
            events, cursor = md.list_events(status="open", limit=batch_limit)
            for ev in events:
                if ev.category and ev.category.lower() in allowed_cats:
                    allowed_events.append(ev)
            fetched += len(events)
            if not cursor:
                break

        counts: Dict[str, int] = {
            "events_scanned": len(allowed_events),
            "agent_signals": 0,
            "survivors": 0,
            "placed": 0,
            "dry_run": 0,
            "rejected": 0,
        }

        if not allowed_events:
            return counts

        # Step 2: agent scanner pass.
        raw_signals: List[Signal] = agent.find_opportunities(allowed_events)
        counts["agent_signals"] = len(raw_signals)

        processed = 0
        for signal in raw_signals:
            if processed >= MAX_THESES:
                break

            # Step 3: compliance + scanner filter on each agent candidate.
            try:
                market = md.get_market(signal.ticker)
                category = md.category_for_market(market)
            except Exception:
                counts["rejected"] += 1
                continue

            comp = compliance.evaluate(category=category, title=market.title or "")
            if not comp.allowed:
                counts["rejected"] += 1
                continue

            side = signal.side
            bid = market.yes_bid if side == "yes" else market.no_bid
            ask = market.yes_ask if side == "yes" else market.no_ask
            if bid is None or not (MIN_PRICE <= bid):
                counts["rejected"] += 1
                continue

            expiry = market.expected_expiration_time or market.expiration_time
            hours = hours_until(expiry)
            if hours is None or not (MIN_HOURS <= hours <= MAX_HOURS):
                counts["rejected"] += 1
                continue

            if ask is not None and (ask - bid) >= MAX_SPREAD:
                counts["rejected"] += 1
                continue

            vol = volume_fp(market)
            if vol < MIN_VOLUME_FP:
                counts["rejected"] += 1
                continue

            counts["survivors"] += 1
            processed += 1

            # Step 4: agent analyst pass with Polymarket reference.
            candidate = ScanCandidate(
                ticker=market.ticker,
                title=market.title or market.ticker,
                category=category,
                side=side,
                price=bid,
                spread=(ask - bid) if ask else Decimal("0"),
                hours_to_expiry=round(hours, 1),
                volume_fp=vol,
                score=float(bid) * hours,
            )
            poly_ref = poly.fetch_reference(candidate.title)
            refined = agent.evaluate(candidate, poly_ref)

            # Step 5: submit through the gate chain.
            order = _signal_to_proposed(refined, bid)
            try:
                account = portfolio.account_state(order.ticker)
            except Exception:
                counts["rejected"] += 1
                continue

            result = executor.submit(
                order,
                category=category,
                title=market.title or "",
                account=account,
                source="agent",
            )
            counts[result.status] += 1

            if result.status in ("placed", "dry_run"):
                journal.record_position(
                    {
                        "ticker": order.ticker,
                        "side": order.side,
                        "entry_price": order.price,
                        "target_price": order.price * Decimal(str(config.strategy.target_fraction)),
                        "count": result.approved_count or order.count,
                        "order_id": result.client_order_id,
                        "confidence": order.confidence,
                        "expiry": expiry,
                    }
                )

    return counts
