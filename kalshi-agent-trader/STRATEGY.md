# French Open Two-Market Strategy — spec

A disciplined rule set for the tennis match-vs-title screener: **when** to trade
and **how to size** each leg. Built on what we measured, not on hope.

> **α is tunable at runtime** via `--fl-alpha` (or `StrategyParams.fl_alpha`) — no
> code change needed. It is the single dial that sets *direction and conviction*:
> **α > 1 = bet favorites, α < 1 = bet upsets, α = 1 = agree with the market.**
> Default **1.09** (data-fit, favorites-leaning but effectively flat). Treat any
> value that actually fires trades (≳1.2) as a conviction overlay, not a proven
> edge — see the calibration note below.

## What we learned (and why this is shaped the way it is)

- **Match markets are ~fairly priced.** Over 120 settled FO matches, favorites
  won 65.8% (women essentially perfectly calibrated: 73% won at a 74¢ avg).
  ⇒ the *match* leg carries no edge — it's a vehicle, not alpha.
- **Title odds are sticky.** Efficient prices are a martingale; a favorite
  winning an expected match barely moves their title price. ⇒ we do **not** rely
  on a title re-rating.
- **The only documented durable edge is the favourite-longshot bias** — longshot
  title prices are over-priced (strongest in later rounds / majors). ⇒ the edge,
  if any, lives in the **title** leg: lay over-priced longshot titles (FADE), or,
  when a title looks *under*-priced, buy it (gated HEDGE).

## The instrument

Per player, two correlated Kalshi markets:
- **Match** (wins their current match) — `KXATPMATCH` / `KXWTAMATCH`.
- **Title** (wins the tournament) — `KXFOMEN` / `KXFOWOMEN`.

Two legs:
- **FADE** = buy match **YES** + buy title **NO** (back the match favorite, lay the title).
- **HEDGE** = buy match **NO** + buy title **YES** (bet the upset, hold the title ticket).

## Entry logic (when to trade)

1. **Fair value** of the title: de-bias the quoted title-YES price with a
   power-odds transform, `fair = p^α / (p^α + (1−p)^α)`, `α ≥ 1`.
   `α>1` pulls longshots down / favorites up (the favourite-longshot correction).
2. **Edge after fees** on the candidate title leg (per contract):
   - FADE: `edge = (1 − fair) − title_no_ask − fee`
   - HEDGE: `edge = fair − title_yes_ask − fee`, where `fee = 0.07·p·(1−p)`.
   (At most one side can be positive — the overround guarantees it.)
3. **Gate — trade only if:**
   - `edge ≥ min_edge` (default **3 pts** after fees), **and**
   - FADE: title-YES mid `≤ longshot_yes_cap` (default **40¢**); **or**
   - HEDGE: `allow_hedge` is on (fires only when the de-bias flags the title under-priced).
   - Otherwise **PASS**.

## Sizing (how much, per leg)

- **Title leg (the edge):** fractional Kelly. `f* = (q − p)/(1 − p)`, stake =
  `kelly_fraction · f* · bankroll` (default **½-Kelly**).
- **Caps (binding, in order):**
  - per-position: total cost ≤ `max_position_frac · bankroll` (default **5%**),
  - bucket: cumulative FO exposure ≤ `max_bucket_frac · bankroll` (default **20%**),
    allocated greedily by edge across players,
  - no leverage.
- **Match leg (the vehicle):** sized at `match_frac ×` the title-leg stake
  (default **1×**) to preserve the two-market structure; it is **edge-neutral**,
  so dropping it (`match_frac=0`) loses no expected value, only changes the payoff shape.
- **Fees** (Kalshi `ceil(0.07·C·p·(1−p))`) are netted from edge and P&L.
- **Max loss** per setup is the worst terminal outcome (FADE: they win the title,
  or lose the match; HEDGE: win the match but not the title).

## Parameters (moderate defaults)

| Param | Default | Meaning |
|---|---|---|
| `--bankroll` | 10000 | bankroll for Kelly sizing |
| `--fl-alpha` | 1.10 | favourite-longshot de-bias (**UNCALIBRATED**) |
| `--kelly` | 0.5 | Kelly fraction |
| `--min-edge` | 0.03 | min per-contract edge after fees |
| `max_position_frac` | 0.05 | per-setup cap (code/config) |
| `max_bucket_frac` | 0.20 | total FO exposure cap (code/config) |
| `--allow-hedge / --no-hedge` | on | gated hedge |

## Run it

```bash
# decision table: what to trade + per-leg sizing
kalshi-trader breakeven --signal --gender both --once --bankroll 25000 --fl-alpha 1.20
# live, refreshing every 15s
kalshi-trader breakeven --signal --bankroll 25000
# screeners behind the signal (payoff shapes)
kalshi-trader breakeven            # hedge + fade grids
kalshi-trader breakeven --tight    # 3-column at-a-glance
```

## ⚠️ `fl_alpha` is the entire edge — here's what the data says

We fit `α` by maximum likelihood on **239 settled match markets** across all
rounds (`scripts/calibrate_alpha.py`):

- **α\* = 1.09** (favorites-side), but **LR vs α=1 is only 0.3** — statistically
  indistinguishable from 1.0. The match market is **well calibrated**; actual win
  rates track prices to within ~2pts across every bucket.
- Gender split (noisy, n≈120 each): **men α\*=1.28**, **women α\*=0.95** — they
  disagree, a sign the signal is mostly noise.

**Implication:** the data does **not** support a tradable edge. The default
`α=1.09` is favorites-leaning but **effectively flat — it fires no trades** after
fees/spread (need ~α≥1.2 before anything clears the 3pt gate). Raising `α` to
make it trade is a **conscious conviction overlay, not a measured edge.**

Re-run the fit as more matches settle:
```bash
python scripts/calibrate_alpha.py 150
```
Until the fit is both >1 **and** significant (LR ≳ 4), treat signals as a small
conviction book at most, and run execution commands with `--dry-run`.

## Not modeled / known gaps

- Exit fees (HEDGE plans to sell the title leg → another taxed trade).
- Order-book depth — longshot title legs are thin; the ask won't fill on size.
  Check `kalshi-trader orderbook TICKER` before executing.
- Capital lockup — the title leg settles at tournament end.
- Correlation beyond the single bucket cap (surface, weather, upset clustering).
