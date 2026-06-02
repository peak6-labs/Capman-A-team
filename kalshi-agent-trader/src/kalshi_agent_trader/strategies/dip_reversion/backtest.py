"""Compatibility exports for the canonical root dip backtest engine."""

from ...dip_backtest import (
    BacktestStats,
    Bar,
    DipTrade,
    aggregate,
    replay_episode,
    seed_anchor,
)

__all__ = [
    "BacktestStats",
    "Bar",
    "DipTrade",
    "aggregate",
    "replay_episode",
    "seed_anchor",
]
