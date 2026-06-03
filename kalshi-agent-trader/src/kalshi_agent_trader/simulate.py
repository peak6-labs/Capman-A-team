"""Bracket Monte-Carlo fair-value model for tennis progression markets.

Why this exists
---------------
Kalshi prices several *derived* markets off the same draw: tournament winner,
"reach the final", "reach the semifinal". These are thin and sticky, so they
often fail to propagate a single match probability cleanly through the bracket.
This module computes model fair values by simulating the draw, then the caller
diffs them against the live market to surface aggregation inconsistencies.

Calibration (the important part)
--------------------------------
Raw clay Elo does NOT recover Kalshi's *match* prices for fast-rising players
(measured: off by ~13pts on Fonseca/Berrettini/Kostyuk at 2026 RG). So we do
not trust raw Elo for match probabilities. Instead, for every matchup that has
a live market we **calibrate** the two players' ratings to reproduce the market
prob (holding the pair's average rating fixed, fitting only the spread). Future
matchups (later SFs, the final) — which have no market yet — then use those
market-consistent ratings. Net effect: the sim reproduces today's match markets
by construction, so divergences in the title/reach-final outputs are genuine
bracket-math inconsistencies, not match-pricing disagreements.

Data-driven
-----------
The draw (rounds, matches, byes) and ratings are declarative data loaded from
``draws/*.yaml`` and ``ratings/*.yaml`` — adding a tournament/round is a new file,
not code. ``simulate_draw`` walks an arbitrary single-elimination bracket; the
engine here stays pure (no I/O beyond the YAML loaders).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Union

import yaml


def match_prob(elo_a: float, elo_b: float) -> float:
    """Logistic Elo: probability A beats B."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def implied_elo_diff(p: float) -> float:
    """Elo difference (A-B) that yields P(A beats B) = p."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / p - 1.0)


@dataclass(frozen=True)
class Matchup:
    """A scheduled match with a live market prob for the first-named player."""
    a: str
    b: str
    market_p_a: float  # market mid: P(a beats b)


def calibrate_ratings(raw: Dict[str, float], anchored: List[Matchup]) -> Dict[str, float]:
    """Adjust each anchored pair to reproduce its market prob.

    Holds the pair's average raw rating fixed and sets the spread so the model
    match prob equals the market's. Players without a market keep raw Elo.
    """
    out = dict(raw)
    for m in anchored:
        mid = (raw[m.a] + raw[m.b]) / 2.0
        d = implied_elo_diff(m.market_p_a)  # = R[a] - R[b]
        out[m.a] = mid + d / 2.0
        out[m.b] = mid - d / 2.0
    return out


@dataclass
class DrawResult:
    reach_sf: Dict[str, float]
    reach_final: Dict[str, float]
    win_title: Dict[str, float]


# --- Declarative draw -------------------------------------------------------- #

@dataclass(frozen=True)
class Draw:
    """A tournament draw from some round onward, loaded from YAML.

    ``rounds`` is ordered earliest-first. Each round has ``matches`` (pairs of
    player names or ``"winner:<ROUND>:<idx>"`` references to a prior match) and
    optional ``byes`` (players who sit out that round but are still alive).
    ``roles`` maps the SF / final markets to round names (e.g. sf: SF, final: F);
    the title is always the winner of the last round's first match.
    """
    gender: str
    events: Dict[str, str]        # {"win":..., "final":..., "sf":...}
    match_series: str             # series ticker for discovering live QF anchors
    players: Tuple[str, ...]
    rounds: Tuple[dict, ...]
    roles: Dict[str, str]         # {"sf": "<round name>", "final": "<round name>"}


def load_draw(path: Union[str, Path]) -> Draw:
    data = yaml.safe_load(Path(path).read_text())
    return Draw(
        gender=data["gender"],
        events=data["events"],
        match_series=data["match_series"],
        players=tuple(data["players"]),
        rounds=tuple(data["rounds"]),
        roles=data["roles"],
    )


def load_ratings(path: Union[str, Path]) -> Dict[str, float]:
    return {k: float(v) for k, v in yaml.safe_load(Path(path).read_text()).items()}


def _deref(token: str, winners: Dict[Tuple[str, int], str]) -> str:
    """Resolve a match-slot token to a concrete player for this trial."""
    if token.startswith("winner:"):
        _, rnd, idx = token.split(":")
        return winners[(rnd, int(idx))]
    return token


def _anchor_prob(a: str, b: str, anchored: Dict[Tuple[str, str], float],
                 ratings: Dict[str, float]) -> float:
    """P(a beats b): the live-market anchor if present, else calibrated Elo."""
    if (a, b) in anchored:
        return anchored[(a, b)]
    if (b, a) in anchored:
        return 1.0 - anchored[(b, a)]
    return match_prob(ratings[a], ratings[b])


def simulate_draw(
    draw: Draw, ratings: Dict[str, float], anchored: Dict[Tuple[str, str], float],
    n: int = 100_000, seed: int = 7,
) -> DrawResult:
    """Monte-Carlo an arbitrary single-elimination bracket.

    Records, per player, P(participate in each round) and P(win the title).
    ``anchored`` keys are (a, b) -> P(a beats b) from the live match markets.
    """
    rng = random.Random(seed)
    players = list(draw.players)
    reach = {r["name"]: dict.fromkeys(players, 0) for r in draw.rounds}
    champ = dict.fromkeys(players, 0)
    last_round = draw.rounds[-1]["name"]

    for _ in range(n):
        winners: Dict[Tuple[str, int], str] = {}
        for r in draw.rounds:
            rn = r["name"]
            seen = set(r.get("byes", []))
            for idx, (a_tok, b_tok) in enumerate(r["matches"]):
                a, b = _deref(a_tok, winners), _deref(b_tok, winners)
                seen.add(a)
                seen.add(b)
                p = _anchor_prob(a, b, anchored, ratings)
                winners[(rn, idx)] = a if rng.random() < p else b
            for p in seen:
                reach[rn][p] += 1
        champ[winners[(last_round, 0)]] += 1

    sf_round, final_round = draw.roles["sf"], draw.roles["final"]
    return DrawResult(
        reach_sf={k: v / n for k, v in reach[sf_round].items()},
        reach_final={k: v / n for k, v in reach[final_round].items()},
        win_title={k: v / n for k, v in champ.items()},
    )
