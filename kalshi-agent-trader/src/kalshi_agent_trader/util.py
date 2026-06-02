"""Small shared helpers used by the scanner, monitor, and agent strategy.

Extracted here so there is one definition of each — they were previously
copy-pasted across modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def hours_until(dt_str: Optional[str]) -> Optional[float]:
    """Hours from now until an ISO-8601 timestamp. None if missing/unparseable."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return None


def volume_fp(market) -> float:
    """Lifetime fixed-point contract volume of a market; 0.0 if absent/invalid."""
    try:
        return float(getattr(market, "volume_fp", None) or 0)
    except (TypeError, ValueError):
        return 0.0
