"""Compatibility exports for the canonical root dip-reversion detector."""

from ...reversion import (
    Anchor,
    DipParams,
    DipSignal,
    DipTracker,
    _mid,
    _size_dip,
    assess,
)

__all__ = [
    "Anchor",
    "DipParams",
    "DipSignal",
    "DipTracker",
    "_mid",
    "_size_dip",
    "assess",
]
