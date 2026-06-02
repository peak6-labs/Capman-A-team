"""Systematic strategy orchestrator: scan → brain → execute.

Wires the Scanner, Brain, and Executor into a single one-shot run. Every proposal
passes through the full compliance → risk → execution gate chain — the strategy
cannot bypass or override those gates.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional

from .brain import Brain
from .client import KalshiClient
from .compliance import ComplianceGate
from .config import AppConfig
from .execution import Executor
from .journal import Journal
from .market_data import MarketData
from .polymarket import PolymarketClient
from .portfolio import Portfolio
from .risk import RiskGate
from .scanner import Scanner


def run(config: AppConfig, *, live: bool = False, dry_run: Optional[bool] = None) -> Dict[str, int]:
    """Run one full scan → brain → execute cycle.

    Returns a summary dict: scanned, proposed, placed, dry_run, rejected.
    """
    dry_run = config.risk.dry_run if dry_run is None else dry_run
    if live:
        dry_run = False

    with KalshiClient(config) as client, Journal() as journal, PolymarketClient(
        timeout=config.runtime.request_timeout_s,
        verify_ssl=config.runtime.verify_ssl,
    ) as poly:
        md = MarketData(client)
        compliance = ComplianceGate(config.compliance)
        risk = RiskGate(config.risk)
        portfolio = Portfolio(client)
        executor = Executor(client, compliance, risk, journal, dry_run=dry_run)
        scanner = Scanner(md, compliance)
        brain = Brain(poly, strategy=config.strategy)

        candidates = scanner.scan()
        proposals = brain.propose(candidates)

        # Category + title were already resolved by the scanner; index them so the
        # gate loop doesn't re-fetch each market.
        cand_index = {(c.ticker, c.side): c for c in candidates}

        counts: Dict[str, int] = {
            "scanned": len(candidates),
            "proposed": len(proposals),
            "placed": 0,
            "dry_run": 0,
            "rejected": 0,
        }

        for order in proposals:
            cand = cand_index.get((order.ticker, order.side))
            category = cand.category if cand else None
            title = cand.title if cand else order.ticker
            try:
                account = portfolio.account_state(order.ticker)
            except Exception as e:
                print(f"[SKIP] {order.ticker}: {e}")
                counts["rejected"] += 1
                continue

            result = executor.submit(
                order,
                category=category,
                title=title,
                account=account,
                source="systematic",
            )
            counts[result.status] += 1

            # Dry-run decisions are journaled by Executor; only live orders that
            # report an immediate execution become monitorable positions here.
            if result.status == "placed" and result.order_status in ("executed", "filled"):
                journal.record_position(
                    {
                        "ticker": order.ticker,
                        "side": order.side,
                        "action": order.action,
                        "entry_price": order.price,
                        "target_price": order.price * Decimal(str(config.strategy.target_fraction)),
                        "count": result.approved_count or order.count,
                        "order_id": result.client_order_id,
                        "confidence": order.confidence,
                    }
                )

    return counts
