# Validation log — title-edge

The bracket draw-sim (`simulate.py` / `sim_screen.py`) is **proven coherent, not profitable**.
Before wiring its title edge into trade sizing, the edge must survive a validation gate. This
file records what's been tested.

## Gate 1 — light convergence study (2026-06-03)

**Question:** when a player's live title price diverges from its match-implied fair
(`fair = C · P(win match)`, with `C` anchored at the start of each live match window), does it
**converge back** over the next horizon? If yes, the title book over-reacts to in-match swings
and the divergence is tradeable. (Bracket-free proxy; the candle history — May 24 → ~Jun 2 —
predates the QF/SF draw the sim encodes, so the sim itself couldn't be reconstructed historically.)

**Method:** `convergence-backtest` over `data/dip_candles/`. Anchor `C = title/match` at the first
aligned candle of each match window; take non-overlapping trades whenever
`|title − C·match| ≥ threshold`; exit after the horizon; score convergence, win rate, and
fee-aware P&L (round-trip ≈ `2·0.07·p·(1−p)`).

**Result (sensitivity sweep):**

| threshold | horizon | n | convergence rate | win rate | mean P&L (¢) |
|---|---|---|---|---|---|
| 0.03 | 30m | 159 | 0.43 | 0.35 | +0.4 |
| 0.03 | 60m | 104 | 0.45 | 0.39 | +1.4 |
| 0.03 | 120m | 72 | 0.43 | 0.40 | +2.8 |
| 0.05 | 60m | 85→42 | 0.41 | 0.39–0.45 | +1.5–4.9 |
| 0.08 | 120m | 24 | 0.38 | 0.46 | +8.5 |

**Verdict: NOT VALIDATED — do not wire the title edge into sizing.**
- **Convergence rate is systematically < 0.50 across every cut** (0.37–0.46). Title divergences
  did *not* reliably revert; if anything they continued. This is the decisive number.
- Win rate is also < 0.50 everywhere. The mildly positive mean P&L (growing with
  threshold/horizon) is therefore **tail-driven** — a few large reversions among many small
  adverse moves — i.e. a fragile, fat-tailed profile, not a reliable edge, at small and
  autocorrelated `n`.
- Consistent with the project's flat-EV prior. The gate did its job.

**Caveats / what could change it:** the anchor `C` is taken at the very first candle of each
window (noisy); a pre-match or smoothed anchor might score differently. `n` is small and
intra-match trades are correlated. This is an *indirect* proxy for the bracket sim's title edge,
not a direct test of it.

**Next options (not yet done):** (a) better anchor + a robustness re-run; (b) forward-test live —
log `simulate --json` edges each run and check convergence/resolution as the SF/final play out
(pairs with `analysis/calibration.py`); (c) fetch QF/SF-era candles and test the sim directly.
