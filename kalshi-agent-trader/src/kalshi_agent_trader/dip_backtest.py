"""Backtest the intraday title-dip strategy on historical 1-min candlesticks.

Purpose: stop guessing ``p_revert`` and ``stop_loss``. For each completed French
Open match we replay the *exact* detection + sizing logic from reversion.py over
the minute bars, then simulate the trade forward to a realistic exit, pricing
fills at executable quotes (buy the ask, sell the bid) net of Kalshi round-trip
fees. Aggregating across every FO match tells us:

  * the **empirical reversion rate** — what ``p_revert`` should actually be;
  * the **loss when wrong** — what ``stop_loss`` is realistic;
  * whether the edge is net-positive after fees at executable prices, at all.

This module is pure (takes pre-fetched bars); scripts/backtest_dips.py does I/O.

A "dip" is *detected* the first minute the title sits ``residual_threshold``
below the match-implied fair while the match is still above ``recover_floor``
(independent of the EV/Kelly gate, so we measure the raw edge). It then exits on
whichever comes first: revert to fair (win), match-floor stop, price stop_loss,
or match end (mark-to-market). ``won`` == reverted to fair.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from statistics import median
from typing import List, Optional, Sequence

from .reversion import Anchor, DipParams, _mid, _size_dip

D = Decimal


def _fee_amt(contracts: Decimal, price: Decimal, rate: Decimal, frac: Decimal) -> Decimal:
    """Kalshi fee for a fill, ceiled to the cent. ``frac``=1 taker, 0.25 maker
    (often rounds to $0 at small size — exactly the maker advantage we're testing)."""
    if contracts <= 0 or price <= 0 or price >= 1:
        return D("0")
    raw = frac * rate * contracts * price * (D("1") - price)
    return (raw * 100).to_integral_value(rounding=ROUND_CEILING) / 100


@dataclass(frozen=True)
class Bar:
    ts: int
    match_bid: Optional[Decimal]
    match_ask: Optional[Decimal]
    title_bid: Optional[Decimal]
    title_ask: Optional[Decimal]


@dataclass(frozen=True)
class DipTrade:
    player: str
    entry_ts: int
    entry_ask: Decimal
    fair_at_entry: Decimal
    residual: Decimal
    phi: Decimal
    exit_ts: int
    exit_px: Decimal           # the bid we sold into
    reason: str                # "revert" | "stop_match" | "stop_loss" | "settle"
    won: bool                  # reason == "revert"
    cents_pnl: Decimal         # per-contract net P&L (sizing-independent edge), dollars/contract
    stake: Decimal             # capital deployed at the chosen sizing
    contracts: Decimal
    dollar_pnl: Decimal        # realized $ at the chosen sizing, net of round-trip fees


def _fee_pc(p: Decimal, rate: Decimal) -> Decimal:
    """Smooth (un-rounded) per-contract fee, for the cents-level edge metric."""
    return rate * p * (D("1") - p)


def seed_anchor(
    bars: Sequence[Bar], name: str, params: DipParams, pre_match_cutoff_ts: int,
) -> Optional[Anchor]:
    """Pre-match baseline: median title/match ratio over bars older than the
    cutoff (everything ≥ ~4h before settle is pre-match for any tennis match).
    Returns None if the player wasn't a favourite (so we never track underdogs)."""
    pre = [b for b in bars if b.ts <= pre_match_cutoff_ts]
    if not pre:
        pre = list(bars[:30])
    matches = [_mid(b.match_bid, b.match_ask) for b in pre]
    titles = [_mid(b.title_bid, b.title_ask) for b in pre]
    matches = [m for m in matches if m]
    titles = [t for t in titles if t]
    if not matches or not titles:
        return None
    mm = D(str(round(float(median(matches)), 4)))
    tm = D(str(round(float(median(titles)), 4)))
    if mm < params.min_match_anchor or tm <= 0:
        return None
    return Anchor(competitor_id="bt", name=name, gender="?",
                  c_ratio=tm / mm, match_mid=mm, title_mid=tm)


def replay_episode(
    bars: Sequence[Bar], name: str, params: DipParams, pre_match_cutoff_ts: int,
    *, maker: bool = False, fill_window: int = 8,
) -> Optional[DipTrade]:
    """Detect the first dip and simulate it to exit. None if no anchor / no dip / no fill.

    ``maker=False`` (default): take liquidity — enter at the ask, exit a revert at
    the bid (full taker fees). ``maker=True``: provide liquidity — enter by resting
    a bid at the current best bid (filled only if price comes to you within
    ``fill_window`` bars, else the trade is *missed*), and exit a revert by resting
    an offer at the entry's match-implied fair (filled if the ask rises to it).
    Maker legs pay ``maker_fee_frac`` of the taker fee; stops/settles cross (taker).
    """
    bars = sorted(bars, key=lambda b: b.ts)
    anc = seed_anchor(bars, name, params, pre_match_cutoff_ts)
    if anc is None:
        return None

    # --- detection: first minute the title is `threshold` below match-implied fair ---
    entry_i = None
    for i, b in enumerate(bars):
        if b.ts <= pre_match_cutoff_ts:
            continue
        mmid = _mid(b.match_bid, b.match_ask)
        if mmid is None or b.title_ask is None or b.title_bid is None:
            continue
        tmid = _mid(b.title_bid, b.title_ask)
        if (anc.c_ratio * mmid) - tmid >= params.residual_threshold and mmid >= params.recover_floor:
            entry_i = i
            break
    if entry_i is None:
        return None

    b0 = bars[entry_i]
    fair0 = anc.c_ratio * _mid(b0.match_bid, b0.match_ask)
    t0mid = _mid(b0.title_bid, b0.title_ask)
    residual0 = fair0 - t0mid
    d_title = t0mid - anc.title_mid
    phi = max(D("0"), min(D("1"), residual0 / (-d_title))) if d_title < 0 else D("0")

    # --- entry fill ---
    if maker:
        entry_px = b0.title_bid                       # rest a bid at the touch
        start = None
        for j in range(entry_i + 1, min(entry_i + 1 + fill_window, len(bars))):
            if bars[j].title_bid is not None and bars[j].title_bid <= entry_px:
                start = j + 1                          # filled when price came to us
                break
        if start is None:
            return None                                # never filled — missed the dip
    else:
        entry_px = b0.title_ask                        # lift the ask
        start = entry_i + 1

    _, _, _, _, stake, contracts = _size_dip(
        ask=entry_px, fair=fair0, c_ratio=anc.c_ratio, phi=phi,
        params=params, bucket_room=D("1e12"),
    )
    target = fair0                                     # maker exit offer (revert to fair)
    fr, mf = params.fee_rate, params.maker_fee_frac

    def close(ts: int, exit_px: Decimal, reason: str, exit_maker: bool) -> DipTrade:
        efr = mf if maker else D("1")
        xfr = mf if exit_maker else D("1")
        cents = (exit_px - entry_px) - efr * _fee_pc(entry_px, fr) - xfr * _fee_pc(exit_px, fr)
        dollar = (contracts * (exit_px - entry_px)
                  - _fee_amt(contracts, entry_px, fr, efr)
                  - _fee_amt(contracts, exit_px, fr, xfr))
        return DipTrade(
            player=name, entry_ts=b0.ts, entry_ask=entry_px, fair_at_entry=fair0,
            residual=residual0, phi=phi, exit_ts=ts, exit_px=exit_px, reason=reason,
            won=(reason == "revert"), cents_pnl=cents, stake=stake,
            contracts=contracts, dollar_pnl=dollar,
        )

    # --- exit: revert (win) / match-floor stop / price stop / settle ---
    for b in bars[start:]:
        mmid = _mid(b.match_bid, b.match_ask)
        if b.title_bid is None or mmid is None:
            continue
        if mmid < params.recover_floor:
            return close(b.ts, b.title_bid, "stop_match", False)     # cross out (taker)
        if b.title_bid <= entry_px - params.stop_loss:
            return close(b.ts, b.title_bid, "stop_loss", False)      # cross out (taker)
        if maker:
            if b.title_ask is not None and b.title_ask >= target:
                return close(b.ts, target, "revert", True)           # our offer got lifted
        elif (anc.c_ratio * mmid) - _mid(b.title_bid, b.title_ask) <= params.exit_band:
            return close(b.ts, b.title_bid, "revert", False)         # sell into the bid
    return close(bars[-1].ts, bars[-1].title_bid, "settle", False)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BacktestStats:
    n: int
    n_revert: int
    n_stop_match: int
    n_stop_loss: int
    n_settle: int
    win_rate: float                # n_revert / n  -> the empirical p_revert
    avg_cents_win: Decimal         # mean per-contract ¢ P&L on winners
    avg_cents_loss: Decimal        # mean per-contract ¢ P&L on non-winners (≤0 typically)
    mean_cents: Decimal            # mean per-contract ¢ P&L over all trades (the edge)
    total_dollar_pnl: Decimal      # summed realized $ at the chosen sizing
    mean_dollar_pnl: Decimal
    suggested_stop_loss: Decimal   # 75th-pct adverse move among losers (a realistic cut)


def aggregate(trades: Sequence[DipTrade]) -> Optional[BacktestStats]:
    if not trades:
        return None
    n = len(trades)
    rev = [t for t in trades if t.reason == "revert"]
    losers = [t for t in trades if t.reason != "revert"]
    z = D("0")

    def mean(xs):
        return sum(xs, z) / D(len(xs)) if xs else z

    # A realistic stop: the 75th-percentile adverse per-contract move among losers
    # (entry - worst exit). Caps most losses while not whipsawing on noise.
    adverse = sorted((t.entry_ask - t.exit_px) for t in losers)
    if adverse:
        idx = min(len(adverse) - 1, (3 * len(adverse)) // 4)
        suggested = adverse[idx]
    else:
        suggested = z

    return BacktestStats(
        n=n, n_revert=len(rev),
        n_stop_match=sum(1 for t in trades if t.reason == "stop_match"),
        n_stop_loss=sum(1 for t in trades if t.reason == "stop_loss"),
        n_settle=sum(1 for t in trades if t.reason == "settle"),
        win_rate=len(rev) / n,
        avg_cents_win=mean([t.cents_pnl for t in rev]),
        avg_cents_loss=mean([t.cents_pnl for t in losers]),
        mean_cents=mean([t.cents_pnl for t in trades]),
        total_dollar_pnl=sum((t.dollar_pnl for t in trades), z),
        mean_dollar_pnl=mean([t.dollar_pnl for t in trades]),
        suggested_stop_loss=suggested,
    )
