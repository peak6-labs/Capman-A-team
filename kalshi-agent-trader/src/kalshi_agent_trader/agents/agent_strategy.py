"""Agent-enhanced pipeline: Claude triages market snapshots, then evaluates survivors.

Pipeline:
  1. List open events filtered to compliance-allowed categories (reduces Claude input size).
  2. Build a compact quote-aware market context for those events.
  3. Agent triages the market snapshots.
  4. For each signal: fetch the Kalshi market and run compliance + scanner filters.
  5. Agent evaluates each survivor with Polymarket reference (evaluate).
  6. Build ProposedOrder from the refined Signal and submit through the gate chain.

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
from ..scanner import MAX_HOURS, MAX_SPREAD, MIN_HOURS, MIN_VOLUME_FP, ScanCandidate
from ..sportsbook_scrape import (
    TargetedSportsbookScraper,
    blended_signal_from_quotes,
    reference_prob_for_signal,
)
from ..util import hours_until, volume_fp
from .analyst_agent import AnalystAgent
from .base import MarketContext, Signal
from .scout_agent import ScoutAgent

AGENT_MARKET_CONTEXT_LIMIT = 120
AGENT_LONG_HORIZON_MIN_VOLUME_FP = 1_000.0
AGENT_LONG_HORIZON_MAX_SPREAD = Decimal("0.20")


def _signal_to_proposed(signal: Signal, market_price: Decimal) -> ProposedOrder:
    return ProposedOrder(
        ticker=signal.ticker,
        side=signal.side,
        action=signal.recommended_action,
        price=market_price,
        count=1,            # risk gate will clamp to fit caps
        fair_prob=signal.fair_prob,
        confidence=signal.confidence,
        rationale=signal.audit_rationale(),
        main_risk=signal.main_risk,
        resolution_risk=signal.resolution_risk,
        liquidity_risk=signal.liquidity_risk,
        news_dependency=signal.news_dependency,
    )


def _float_decimal(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value)


def _spread(bid: Optional[Decimal], ask: Optional[Decimal]) -> Optional[Decimal]:
    if bid is None or ask is None:
        return None
    return ask - bid


def _market_context_score(ctx: MarketContext) -> float:
    """Prioritize fast, useful Claude context over exhaustive market coverage."""
    score = min(ctx.volume_fp, 100_000.0) / 100_000.0
    if ctx.best_spread is not None:
        score += max(0.0, 0.25 - ctx.best_spread)
    prices = [p for p in (ctx.yes_bid, ctx.no_bid) if p is not None]
    if any(0.01 <= p <= 0.15 for p in prices):
        score += 0.25
    if ctx.hours_to_expiry is not None and 4.0 <= ctx.hours_to_expiry <= 72.0:
        score += 0.15
    return score


def _is_trade_action(action: str) -> bool:
    return action in {"buy", "sell"}


def _entry_quote(market, side: str, action: str) -> tuple[Optional[Decimal], Optional[Decimal], Decimal]:
    """Return entry price, opposite quote, and spread for a proposed action."""
    bid = market.yes_bid if side == "yes" else market.no_bid
    ask = market.yes_ask if side == "yes" else market.no_ask
    spread = (ask - bid) if ask is not None and bid is not None else Decimal("0")
    price = ask if action == "buy" else bid
    opposite = bid if action == "buy" else ask
    return price, opposite, spread


def _agent_entry_allowed(
    *,
    price: Optional[Decimal],
    spread: Decimal,
    hours: Optional[float],
    volume: float,
) -> bool:
    """Agent-specific market quality gate.

    The systematic scanner is short-dated by design. Agent trades may be long-dated
    when the book is tight and historically active, which is common in politics.
    """
    if price is None or price <= 0 or price >= 1:
        return False
    if spread >= MAX_SPREAD:
        return False
    if volume < MIN_VOLUME_FP:
        return False
    if hours is None:
        return False
    if MIN_HOURS <= hours <= MAX_HOURS:
        return True
    return volume >= AGENT_LONG_HORIZON_MIN_VOLUME_FP and spread <= AGENT_LONG_HORIZON_MAX_SPREAD


def _build_market_context(market, category: Optional[str], series: str = "") -> MarketContext:
    yes_spread = _spread(market.yes_bid, market.yes_ask)
    no_spread = _spread(market.no_bid, market.no_ask)
    spreads = [s for s in (yes_spread, no_spread) if s is not None]
    expiry = market.expected_expiration_time or market.expiration_time
    hours = hours_until(expiry)
    return MarketContext(
        ticker=market.ticker,
        event_ticker=market.event_ticker,
        title=market.title or market.ticker,
        category=category,
        series=series,
        yes_bid=_float_decimal(market.yes_bid),
        yes_ask=_float_decimal(market.yes_ask),
        no_bid=_float_decimal(market.no_bid),
        no_ask=_float_decimal(market.no_ask),
        yes_spread=_float_decimal(yes_spread),
        no_spread=_float_decimal(no_spread),
        best_spread=_float_decimal(min(spreads)) if spreads else None,
        volume_fp=float(volume_fp(market)),
        liquidity=float(market.liquidity or 0),
        hours_to_expiry=None if hours is None else round(hours, 1),
    )


def run_agent_strategy(
    config: AppConfig,
    *,
    live: bool = False,
    dry_run: Optional[bool] = None,
    max_events: int = 50,
) -> Dict[str, int]:
    """Run one agent-enhanced scan → evaluate → execute cycle."""
    dry_run = config.risk.dry_run if dry_run is None else dry_run
    if live:
        dry_run = False
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
        api_key = config.secrets.anthropic_api_key or ""
        scout = ScoutAgent(api_key=api_key, model=config.models.scout_model)
        analyst = AnalystAgent(api_key=api_key, model=config.models.analyst_model)
        sportsbook = TargetedSportsbookScraper(config.sportsbook_scrape)

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
            "markets_scanned": 0,
            "agent_signals": 0,
            "survivors": 0,
            "placed": 0,
            "dry_run": 0,
            "rejected": 0,
        }

        if not allowed_events:
            return counts

        # Step 2: build a fast, quote-aware market context for the Sonnet triage pass.
        market_contexts: List[MarketContext] = []
        for ev in allowed_events:
            try:
                markets, _ = md.list_markets(
                    status="open", event_ticker=ev.event_ticker, limit=100
                )
            except Exception:
                counts["rejected"] += 1
                continue
            for market in markets:
                ctx = _build_market_context(
                    market, category=ev.category, series=ev.series_ticker or ""
                )
                # Keep dead/stale-looking markets out of the prompt unless they
                # have enough traded volume to be worth an explicit agent read.
                if (
                    ctx.volume_fp <= 0
                    and (ctx.best_spread is None or ctx.best_spread > 0.25)
                ):
                    continue
                market_contexts.append(ctx)

        market_contexts.sort(key=_market_context_score, reverse=True)
        market_contexts = market_contexts[:AGENT_MARKET_CONTEXT_LIMIT]
        counts["markets_scanned"] = len(market_contexts)

        # Step 3: scout pass over live market snapshots (cheap triage tier).
        raw_signals: List[Signal] = scout.find_market_opportunities(market_contexts)
        counts["agent_signals"] = len(raw_signals)

        processed = 0
        for signal in raw_signals:
            if processed >= MAX_THESES:
                break

            if not _is_trade_action(signal.recommended_action):
                counts["rejected"] += 1
                journal.record_decision(
                    outcome="rejected",
                    source="agent:triage",
                    market_ticker=signal.ticker,
                    side=signal.side,
                    fair_prob=signal.fair_prob,
                    confidence=signal.confidence,
                    rationale=signal.audit_rationale(),
                    gate="agent",
                    reason=f"agent recommended {signal.recommended_action}",
                )
                continue

            # Step 4: compliance + scanner filter on each agent candidate.
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
            expiry = market.expected_expiration_time or market.expiration_time
            hours = hours_until(expiry)
            vol = volume_fp(market)
            entry_price, _, spread = _entry_quote(market, side, signal.recommended_action)
            if not _agent_entry_allowed(
                price=entry_price,
                spread=spread,
                hours=hours,
                volume=vol,
            ):
                counts["rejected"] += 1
                continue

            counts["survivors"] += 1
            processed += 1

            # Step 5: agent analyst pass with Polymarket reference.
            candidate = ScanCandidate(
                ticker=market.ticker,
                title=market.title or market.ticker,
                category=category,
                side=side,
                price=entry_price,
                spread=spread,
                hours_to_expiry=round(hours, 1),
                volume_fp=vol,
                score=float(entry_price) * hours,
            )
            poly_ref = poly.fetch_reference(candidate.title)
            refined = analyst.evaluate(candidate, poly_ref, action=signal.recommended_action)
            if refined.recommended_action != signal.recommended_action:
                counts["rejected"] += 1
                journal.record_decision(
                    outcome="rejected",
                    source="agent:analyst",
                    market_ticker=market.ticker,
                    side=refined.side,
                    target_price=entry_price,
                    fair_prob=refined.fair_prob,
                    confidence=refined.confidence,
                    rationale=refined.audit_rationale(),
                    gate="agent",
                    reason=(
                        f"agent changed action from {signal.recommended_action} "
                        f"to {refined.recommended_action}"
                    ),
                )
                continue

            # Optional targeted scrape: only after the agent has produced a
            # prospective Kalshi trade, and only for URLs configured for this ticker.
            sportsbook_quotes = sportsbook.fetch_for_signal(refined)
            for quote in sportsbook_quotes:
                yes_prob = (
                    quote.implied_prob
                    if quote.side.lower() == "yes"
                    else Decimal("1") - quote.implied_prob
                )
                journal.record_reference_quote(
                    {
                        "source": quote.source,
                        "event_key": market.event_ticker,
                        "market_ticker": market.ticker,
                        "question": f"{quote.outcome} @ {quote.url}",
                        "yes_prob": yes_prob,
                        "confidence": quote.confidence,
                        "raw": {
                            "american_odds": quote.american_odds,
                            "side": quote.side,
                            "snippet": quote.snippet,
                        },
                    }
                )

            if config.sportsbook_scrape.enabled:
                if config.sportsbook_scrape.require_quote_for_agent and not sportsbook_quotes:
                    counts["rejected"] += 1
                    journal.record_decision(
                        outcome="rejected",
                        source="agent:sportsbook_scrape",
                        market_ticker=market.ticker,
                        side=refined.side,
                        fair_prob=refined.fair_prob,
                        confidence=refined.confidence,
                        rationale=refined.rationale,
                        gate="sportsbook_scrape",
                        reason="no configured sportsbook quote parsed",
                    )
                    continue

                if sportsbook_quotes:
                    ref_probs = [
                        reference_prob_for_signal(refined, q)
                        for q in sportsbook_quotes
                    ]
                    avg_ref = sum(ref_probs, Decimal("0")) / Decimal(len(ref_probs))
                    disagreement = abs(avg_ref - Decimal(str(refined.fair_prob)))
                    max_disagreement = config.sportsbook_scrape.max_reference_disagreement
                    if (
                        max_disagreement is not None
                        and disagreement > Decimal(str(max_disagreement))
                    ):
                        counts["rejected"] += 1
                        journal.record_decision(
                            outcome="rejected",
                            source="agent:sportsbook_scrape",
                            market_ticker=market.ticker,
                            side=refined.side,
                            fair_prob=refined.fair_prob,
                            confidence=refined.confidence,
                            rationale=refined.rationale,
                            gate="sportsbook_scrape",
                            reason=f"sportsbook disagreement {disagreement:.3f} > {max_disagreement}",
                        )
                        continue
                    refined = blended_signal_from_quotes(
                        refined,
                        sportsbook_quotes,
                        blend_weight=config.sportsbook_scrape.blend_weight,
                    )

            # Step 6: submit through the gate chain.
            order = _signal_to_proposed(refined, entry_price)
            try:
                account = portfolio.account_state(order.ticker)
            except Exception:
                counts["rejected"] += 1
                continue

            result_ex = executor.submit(
                order,
                category=category,
                title=market.title or "",
                account=account,
                source="agent:sportsbook_scrape" if sportsbook_quotes else "agent",
            )
            counts[result_ex.status] += 1

            if result_ex.status == "placed" and result_ex.order_status in ("executed", "filled"):
                journal.record_position(
                    {
                        "ticker": order.ticker,
                        "side": order.side,
                        "action": order.action,
                        "entry_price": order.price,
                        "target_price": order.price * Decimal(str(config.strategy.target_fraction)),
                        "count": result_ex.approved_count or order.count,
                        "order_id": result_ex.client_order_id,
                        "confidence": order.confidence,
                        "expiry": expiry,
                    }
                )

    return counts
