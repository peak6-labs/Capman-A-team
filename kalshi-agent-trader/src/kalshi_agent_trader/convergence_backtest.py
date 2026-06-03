"""Light convergence backtest for the title-vs-match over-reaction hypothesis.

Validation, not a strategy. It does NOT reconstruct the bracket. For each live
match window it anchors a player's conditional ``C = title / P(win match)`` at the
start of the window, then asks: when the live title price later *diverges* from the
match-implied fair ``C · P(win match)`` by more than a threshold, does it converge
back over the next horizon? This is the bracket-free, "weaker evidence" check the
user chose — and it is the same over-reaction logic as the (net-positive) intraday
dip-reversion edge, repurposed as a test.

Inputs are the local Kalshi candle snapshots in ``data/dip_candles/`` (gitignored):
  - title markets:  KXFOMEN-26-<CODE>_<start>_<end>.json / KXFOWOMEN-26-<CODE>_...
  - match markets:  KX{ATP,WTA}MATCH-26<DATE><MATCHUP>-<CODE>_<start>_<end>.json

The scoring core (``score_window``) is pure and unit-tested with synthetic series;
file IO wraps it. Read-only; places nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

Series = List[Tuple[int, float]]            # (end_period_ts, yes_mid), sorted by ts
FEE_RATE = 0.07                             # Kalshi fee coefficient (per leg)
_ALIGN_TOL_S = 180                          # match/title candle timestamp tolerance


# --- candle parsing --------------------------------------------------------- #

def _mid(candle: dict) -> Optional[float]:
    """Best available YES mid for a candle: bid/ask mid, else a price field."""
    yb = (candle.get("yes_bid") or {}).get("close_dollars")
    ya = (candle.get("yes_ask") or {}).get("close_dollars")
    try:
        fb, fa = float(yb), float(ya)
        if fa > 0:
            return (fb + fa) / 2.0
    except (TypeError, ValueError):
        pass
    price = candle.get("price") or {}
    for k in ("mean_dollars", "close_dollars", "previous_dollars"):
        v = price.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def load_series(path: Path) -> Series:
    candles = json.loads(Path(path).read_text())
    out: Series = []
    for c in candles:
        ts = c.get("end_period_ts")
        m = _mid(c)
        if ts is not None and m is not None:
            out.append((int(ts), m))
    out.sort(key=lambda x: x[0])
    return out


_MATCH_RE = re.compile(r"^(KXATPMATCH|KXWTAMATCH)-26[A-Z0-9]+-([A-Z]+)_(\d+)_(\d+)\.json$")
_TITLE_RE = re.compile(r"^(KXFOMEN|KXFOWOMEN)-26-([A-Z]+)_(\d+)_(\d+)\.json$")


def parse_match_file(name: str) -> Optional[Tuple[str, str]]:
    """(gender, player_code) for a match candle file, else None."""
    m = _MATCH_RE.match(name)
    if not m:
        return None
    return ("men" if m.group(1) == "KXATPMATCH" else "women", m.group(2))


def parse_title_file(name: str) -> Optional[Tuple[str, str]]:
    """(gender, player_code) for a title candle file, else None."""
    m = _TITLE_RE.match(name)
    if not m:
        return None
    return ("men" if m.group(1) == "KXFOMEN" else "women", m.group(2))


# --- pure scoring core ------------------------------------------------------ #

@dataclass
class Trade:
    gender: str
    code: str
    divergence: float        # title - fair at entry (signed; >0 = title rich)
    entry_title: float
    exit_title: float
    pnl_net: float           # convergence-side P&L per contract, net of round-trip fee
    converged: bool          # did the title move toward fair over the horizon?


def _lookup(series: Series, t: int, tol: int = _ALIGN_TOL_S) -> Optional[float]:
    """Mid at the candle nearest to ``t`` within ``tol`` seconds, else None."""
    best, best_dt = None, tol + 1
    for ts, mid in series:
        dt = abs(ts - t)
        if dt <= tol and dt < best_dt:
            best, best_dt = mid, dt
    return best


def score_window(
    match: Series, title: Series, *, gender: str, code: str,
    threshold: float = 0.03, horizon_s: int = 3600,
) -> List[Trade]:
    """Score one live-match window for title over-reaction convergence.

    Anchors C = title/match at the first aligned point, then takes non-overlapping
    convergence trades whenever |title - C*match| >= threshold.
    """
    aligned: List[Tuple[int, float, float]] = []
    for ts, m_mid in match:
        t_mid = _lookup(title, ts)
        if t_mid is not None and m_mid > 0:
            aligned.append((ts, m_mid, t_mid))
    if len(aligned) < 2:
        return []

    _, m0, t0 = aligned[0]
    c0 = min(max(t0 / m0, 0.0), 1.0)         # anchored conditional P(title | win match)

    trades: List[Trade] = []
    i = 1
    while i < len(aligned):
        ts_i, m_i, t_i = aligned[i]
        fair = c0 * m_i
        div = t_i - fair
        if abs(div) < threshold:
            i += 1
            continue
        # exit at the first aligned point >= horizon later, else the last point
        j = next((k for k in range(i + 1, len(aligned)) if aligned[k][0] >= ts_i + horizon_s),
                 len(aligned) - 1)
        if j <= i:
            break
        t_j = aligned[j][2]
        gross = (t_i - t_j) if div > 0 else (t_j - t_i)   # fade rich / buy cheap
        fee = 2.0 * FEE_RATE * t_i * (1.0 - t_i)          # round-trip, approx
        trades.append(Trade(
            gender=gender, code=code, divergence=div,
            entry_title=t_i, exit_title=t_j, pnl_net=gross - fee,
            converged=abs(t_j - fair) < abs(t_i - fair),
        ))
        i = j + 1                                          # non-overlapping
    return trades


# --- report ----------------------------------------------------------------- #

@dataclass
class Report:
    threshold: float
    horizon_s: int
    trades: List[Trade] = field(default_factory=list)

    def _stats(self, ts: List[Trade]) -> dict:
        n = len(ts)
        if n == 0:
            return {"n": 0}
        conv = sum(t.converged for t in ts)
        pnl = [t.pnl_net for t in ts]
        return {
            "n": n,
            "convergence_rate": round(conv / n, 3),
            "mean_pnl_net": round(sum(pnl) / n, 4),
            "total_pnl_net": round(sum(pnl), 3),
            "win_rate": round(sum(p > 0 for p in pnl) / n, 3),
            "mean_abs_divergence": round(sum(abs(t.divergence) for t in ts) / n, 4),
        }

    def summary(self) -> dict:
        by_gender = {
            g: self._stats([t for t in self.trades if t.gender == g])
            for g in ("men", "women")
        }
        return {
            "threshold": self.threshold,
            "horizon_s": self.horizon_s,
            "overall": self._stats(self.trades),
            "by_gender": by_gender,
        }


def run_backtest(
    candles_dir: Path, *, threshold: float = 0.03, horizon_s: int = 3600,
) -> Report:
    """Pair each match candle window with its player's title candles and score it."""
    candles_dir = Path(candles_dir)
    files = list(candles_dir.glob("*.json"))

    # title series per (gender, code) — concatenate any multiple files
    titles: Dict[Tuple[str, str], Series] = {}
    for f in files:
        key = parse_title_file(f.name)
        if key:
            titles.setdefault(key, [])
            titles[key].extend(load_series(f))
    for s in titles.values():
        s.sort(key=lambda x: x[0])

    report = Report(threshold=threshold, horizon_s=horizon_s)
    for f in files:
        mkey = parse_match_file(f.name)
        if not mkey:
            continue
        title_series = titles.get(mkey)
        if not title_series:
            continue
        match_series = load_series(f)
        report.trades.extend(score_window(
            match_series, title_series, gender=mkey[0], code=mkey[1],
            threshold=threshold, horizon_s=horizon_s,
        ))
    return report
