# Kalshi Implementation: What's Feasible

## What the API Gives You
- **Read (unauthenticated):** list markets, orderbook, series, event data
- **Read (authenticated):** portfolio balance, open orders, positions, trade history
- **Write (authenticated):** place limit/market orders, cancel orders

## Real Edges on Kalshi

| Edge | Confidence | Notes |
|---|---|---|
| **Longshot bias** | High | Academically documented — sub-10¢ sides are systematically overpriced. Current bot exploits this. |
| **Maker > taker returns** | High | Limit orders beat market orders on Kalshi per Jon-Becker's dataset. Always be a maker. |
| **Category miscalibration** | Medium | Niche/local markets (weather, local elections) are less efficiently priced than crypto/major politics. Filter by category. |
| **Time-of-day patterns** | Medium | `returns_by_hour.py` in the analysis repo shows intraday patterns worth backtesting. |

## What to Ignore
- **LLM probability estimates** — liquid markets are already better forecasters than Claude reading headlines
- **Whale/wallet copying** — no public order attribution on Kalshi
- **Volume spike exits** — you're reacting after the move

## Architecture That Works
```
Scanner → score open markets by category + price + OI
Brain   → calibration-adjusted p_win + Kelly sizing (quarter-Kelly cap)
Executor → limit SELL only, never cross the spread
Exit    → target profit hit OR time decay (no volume signal)
```

## Current State of This Repo
- `client.py` — SDK auth with configurable SSL verify (set `KALSHI_VERIFY_SSL=false` for demo env / Python 3.14)
- `scanner.py` — scans markets with volume, spread, and time filters; writes `queue.json`
- `brain.py` — Kelly-sized probability estimates against `queue.json`; writes `thesis.json`
- `executor.py` — limit SELL from `thesis.json`; tracks open positions in `positions.json`
- `exit_monitor.py` — polls positions and closes on TARGET_HIT / NEAR_EXPIRY / STALE_THESIS / RESOLVED
- `startup.sh` — orchestrates the full scan → brain → execute → monitor loop
- `strategy.py` — lightweight standalone scanner/seller for ad-hoc runs (no Kelly sizing or position tracking)

## Biggest Risk
Tail-selling has a **high win rate but fat left tail** — one wrong market that resolves against you can wipe multiple wins. Position sizing (never more than 1-2% of bankroll per contract) matters more than the entry logic.
