# Trading Edges & Feasibility

Strategy rationale behind the systematic core. Ported from the original
`FEASIBILITY.md` when the flat prototype was retired.

## What the Kalshi API gives you
- **Read (unauthenticated):** list markets, orderbook, series, event data
- **Read (authenticated):** portfolio balance, open orders, positions, trade history
- **Write (authenticated):** place limit/market orders, cancel orders

## Real edges on Kalshi

| Edge | Confidence | Notes |
|---|---|---|
| **Longshot bias** | High | Academically documented — sub-10¢ sides are systematically overpriced. The scanner + brain exploit this. |
| **Maker > taker returns** | High | Limit orders beat market orders on Kalshi. Always be a maker. |
| **Category miscalibration** | Medium | Niche/local markets (weather, local elections) are less efficiently priced than crypto/major politics. Filter by category. |
| **Time-of-day patterns** | Medium | Intraday patterns are worth backtesting before relying on them. |

## What to ignore
- **LLM probability estimates as ground truth** — liquid markets are already better
  forecasters than a model reading headlines. The agents *propose*; deterministic
  gates dispose. (See the agent design in the README.)
- **Whale/wallet copying** — no public order attribution on Kalshi.
- **Volume-spike exits** — you're reacting after the move.

## Architecture that works
```
Scanner → score open markets by category + price + OI
Brain   → calibration-adjusted p_win + Kelly sizing (quarter-Kelly cap)
Executor → limit SELL only, never cross the spread (compliance → risk gated)
Monitor → target profit hit OR time decay (no volume signal)
```

## Biggest risk
Tail-selling has a **high win rate but a fat left tail** — one wrong market that
resolves against you can wipe multiple wins. Position sizing (never more than
1–2% of bankroll per contract) matters more than the entry logic. This is why the
risk gate's per-position cap is non-negotiable and enforced in code.
