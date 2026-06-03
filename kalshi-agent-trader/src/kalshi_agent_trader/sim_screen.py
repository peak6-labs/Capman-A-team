"""Live glue for the bracket draw-sim: discover anchors, simulate, diff vs market.

The pure model lives in ``simulate.py``; the draw/ratings are declarative data
(``draws/*.yaml`` + ``ratings/*.yaml``). This module:

  1. discovers the live match-market anchors for the draw's concrete pairings via
     the ``tennis_screen`` helpers (no hardcoded event tickers),
  2. calibrates ratings to those anchors and runs ``simulate_draw``,
  3. fetches the derived winner / reach-final / reach-SF quotes and diffs them,
  4. attaches a Polymarket title reference per player (live second-venue check).

Edges on the tight WIN market are actionable; wide FINAL/SF quotes are flagged.
``run(json_out=True)`` emits a machine-readable snapshot for the research agent.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich import box
from rich.console import Console, Group
from rich.table import Table

from .client import KalshiClient
from .config import load_config
from .market_data import MarketData
from .polymarket import PolymarketClient
from .simulate import (
    Draw, DrawResult, Matchup, calibrate_ratings, load_draw, load_ratings, simulate_draw,
)
from .tennis_screen import _side_price, fetch_series_markets, normalize_name

console = Console()

_ROOT = Path(__file__).resolve().parents[2]
_DRAWS = {
    "men": _ROOT / "draws" / "rg-2026-men.yaml",
    "women": _ROOT / "draws" / "rg-2026-women.yaml",
}
_RATINGS = _ROOT / "ratings" / "clay_elo.yaml"
_TIGHT = 0.10            # widest spread treated as a tradable two-sided quote
_POLY_TITLE = "French Open"


def _concrete_pairs(draw: Draw) -> List[Tuple[str, str]]:
    """Every match whose both sides are named players (i.e. has a live market)."""
    out: List[Tuple[str, str]] = []
    for r in draw.rounds:
        for a, b in r["matches"]:
            if not a.startswith("winner:") and not b.startswith("winner:"):
                out.append((a, b))
    return out


def _match_for(name: str, sub: str) -> bool:
    return normalize_name(name) in normalize_name(sub)


def discover_anchors(md: MarketData, draw: Draw) -> Dict[Tuple[str, str], float]:
    """For each concrete pairing in the draw, de-vig the live match market → P(a)."""
    markets = fetch_series_markets(md, draw.match_series)
    by_event: Dict[str, List] = {}
    for m in markets:
        if (m.status or "").lower() in ("open", "active"):
            by_event.setdefault(m.event_ticker, []).append(m)

    anchored: Dict[Tuple[str, str], float] = {}
    for a, b in _concrete_pairs(draw):
        for legs in by_event.values():
            if len(legs) != 2:
                continue
            ma = next((m for m in legs if _match_for(a, m.yes_sub_title or "")), None)
            mb = next((m for m in legs if _match_for(b, m.yes_sub_title or "")), None)
            if ma is None or mb is None or ma is mb:
                continue
            a_mid, b_mid = _side_price(ma, "yes", "mid"), _side_price(mb, "yes", "mid")
            if a_mid and b_mid and (a_mid + b_mid) > 0:
                anchored[(a, b)] = float(a_mid / (a_mid + b_mid))
            break
    return anchored


def _quote_event(client, event: str, players: Tuple[str, ...]) -> Dict[str, Tuple[float, bool]]:
    """player -> (mid, reliable). reliable iff two-sided and spread <= _TIGHT."""
    out: Dict[str, Tuple[float, bool]] = {}
    resp = client.get("/markets", params={"event_ticker": event, "limit": 200})
    for m in resp.get("markets", []):
        if m.get("status") != "active":
            continue
        sub = m.get("yes_sub_title") or ""
        try:
            yb, ya = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        for p in players:
            if _match_for(p, sub):
                out[p] = ((yb + ya) / 2.0, (ya - yb) <= _TIGHT)
    return out


def _poly_refs(poly: PolymarketClient, players: Tuple[str, ...]) -> Dict[str, Tuple[float, float]]:
    """player -> (yes_price, similarity) from Polymarket, when a confident match exists."""
    out: Dict[str, Tuple[float, float]] = {}
    for p in players:
        ref = poly.fetch_reference(f"{p} {_POLY_TITLE}")
        if ref is not None:
            out[p] = (float(ref.yes_price), ref.similarity)
    return out


def _build(draw: Draw, ratings: Dict[str, float], anchored: Dict[Tuple[str, str], float],
           quotes: Dict[str, Dict], poly: Dict[str, Tuple[float, float]],
           n: int, seed: int) -> Tuple[DrawResult, List[dict]]:
    R = calibrate_ratings(ratings, [Matchup(a, b, p) for (a, b), p in anchored.items()])
    res = simulate_draw(draw, R, anchored, n=n, seed=seed)
    rows = []
    for k in sorted(res.win_title, key=lambda x: -res.win_title[x]):
        win_q = quotes["win"].get(k)
        kmid = win_q[0] if win_q else None
        pm = poly.get(k)
        rows.append({
            "player": k,
            "model_title": round(res.win_title[k], 4),
            "model_final": round(res.reach_final[k], 4),
            "model_sf": round(res.reach_sf[k], 4),
            "kalshi_mid": kmid,
            "kalshi_reliable": win_q[1] if win_q else None,
            "poly_mid": pm[0] if pm else None,
            "poly_sim": pm[1] if pm else None,
            "edge_vs_kalshi": round(res.win_title[k] - kmid, 4) if kmid is not None else None,
            "edge_vs_poly": round(res.win_title[k] - pm[0], 4) if pm else None,
        })
    return res, rows


def _edge_cell(model: float, q: Optional[Tuple[float, bool]]) -> str:
    if q is None or q[0] is None:
        return f"{model*100:.0f} / —"
    mid, reliable = q
    edge = (model - mid) * 100
    if not reliable:
        return f"{model*100:.0f} / [dim]{mid*100:.0f}~ wide[/dim]"
    color = "green" if edge >= 4 else ("red" if edge <= -4 else "white")
    return f"{model*100:.0f} / {mid*100:.0f}  [{color}]{edge:+.0f}[/{color}]"


def _table(title: str, res: DrawResult, quotes: Dict[str, Dict],
           poly: Dict[str, Tuple[float, float]], note: str) -> Table:
    t = Table(title=title, box=box.SIMPLE_HEAVY, header_style="bold",
              caption=note, expand=False, pad_edge=False)
    for col in ("player", "WIN model/mkt edge", "FINAL model/mkt", "SF model/mkt", "Poly"):
        t.add_column(col, justify="left" if col == "player" else "right")
    for k in sorted(res.win_title, key=lambda x: -res.win_title[x]):
        pm = poly.get(k)
        poly_cell = f"{pm[0]*100:.0f} (s{pm[1]:.2f})" if pm else "—"
        t.add_row(
            k,
            _edge_cell(res.win_title[k], quotes["win"].get(k)),
            _edge_cell(res.reach_final[k], quotes["final"].get(k)),
            _edge_cell(res.reach_sf[k], quotes["sf"].get(k)),
            poly_cell,
        )
    return t


def run(*, gender: str = "both", n: int = 100_000, seed: int = 7,
        json_out: bool = False, use_poly: bool = True) -> None:
    """Discover live anchors, simulate each requested draw, and print edges / JSON."""
    cfg = load_config()
    genders = ("men", "women") if gender == "both" else (gender,)
    ratings = load_ratings(_RATINGS)

    snapshot: Dict[str, dict] = {}
    tables = []
    with KalshiClient(cfg) as client:
        md = MarketData(client)
        poly_client = (
            PolymarketClient(timeout=cfg.runtime.request_timeout_s, verify_ssl=cfg.runtime.verify_ssl)
            if use_poly else None
        )
        try:
            for g in genders:
                draw = load_draw(_DRAWS[g])
                anchored = discover_anchors(md, draw)
                quotes = {kind: _quote_event(client, ev, draw.players)
                          for kind, ev in draw.events.items()}
                poly = _poly_refs(poly_client, draw.players) if poly_client else {}
                res, rows = _build(draw, ratings, anchored, quotes, poly, n, seed)
                snapshot[g] = {
                    "anchored": {f"{a} vs {b}": round(p, 4) for (a, b), p in anchored.items()},
                    "title_sum": round(sum(res.win_title.values()), 4),
                    "rows": rows,
                }
                note = (f"title sum {sum(res.win_title.values()):.2f} (→1.0) • "
                        f"{len(anchored)} live anchors")
                tables.append(_table(f"{g.upper()} — bracket model vs market", res, quotes, poly, note))
        finally:
            if poly_client:
                poly_client.close()

    if json_out:
        print(json.dumps({"strategy": "bracket-sim", "n": n, "seed": seed,
                          "draws": snapshot}, indent=2))
    else:
        console.print(Group(*tables))
