"""Control router: kill-switch + dry_run toggles + exchange/auth status + live signal scan."""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from kalshi_agent_trader.client import KalshiError
from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.market_data import MarketData
from kalshi_agent_trader.risk import RiskGate
from kalshi_agent_trader.util import hours_until, volume_fp

from ..config_writer import (
    clear_kill_switch,
    engage_kill_switch,
    kill_switch_engaged,
    set_dry_run,
)
from ..deps import cached, config_write_lock, get_client, get_config, reset_client

_SCAN_MIN_PRICE = Decimal("0.01")
_SCAN_MAX_PRICE = Decimal("0.10")
_SCAN_MIN_HOURS = 4.0
_SCAN_MAX_HOURS = 48.0
_SCAN_MAX_SPREAD = Decimal("0.50")
_SCAN_MIN_VOLUME = 10.0
_SIGNALS_TTL = 60  # seconds

router = APIRouter(prefix="/control", tags=["control"])


class KillRequest(BaseModel):
    engaged: bool


class DryRunRequest(BaseModel):
    dry_run: bool


@router.get("/status")
def control_status():
    cfg = get_config()
    client = get_client()

    # Exchange status (public, no auth needed).
    try:
        exchange = client.get("/exchange/status")
    except Exception as exc:
        exchange = {"error": str(exc)}

    # Auth probe.
    auth_ok = False
    auth_error = None
    credentials_present = client.can_authenticate()
    if credentials_present:
        try:
            client.get("/portfolio/balance", auth=True)
            auth_ok = True
        except KalshiError as exc:
            auth_error = f"HTTP {exc.status}: {exc.body[:200]}"
        except Exception as exc:
            auth_error = str(exc)

    return {
        "kill_switch_engaged": kill_switch_engaged(),
        "dry_run": cfg.risk.dry_run,
        "exchange": exchange,
        "auth": {
            "credentials_present": credentials_present,
            "auth_ok": auth_ok,
            "error": auth_error,
        },
        "username": os.environ.get("USER") or os.environ.get("USERNAME") or "operator",
    }


@router.post("/kill")
def toggle_kill(req: KillRequest):
    if req.engaged:
        engage_kill_switch()
    else:
        clear_kill_switch()
    return {"kill_switch_engaged": kill_switch_engaged()}


@router.post("/dry-run")
def toggle_dry_run(req: DryRunRequest):
    try:
        actual = set_dry_run(req.dry_run, config_write_lock)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # Reset client so fresh config is picked up next call.
    reset_client()
    return {"dry_run": actual}


@router.get("/signals")
def live_signals(limit: int = Query(default=10, ge=1, le=50)):
    """Scan open markets and return top cheap-tail candidates. Cached for 60 seconds."""
    cfg = get_config()
    client = get_client()

    def _scan() -> dict[str, Any]:
        md = MarketData(client)
        compliance = ComplianceGate(cfg.compliance)
        candidates = []
        total_scanned = 0
        cursor: Optional[str] = None

        for _ in range(20):
            markets, cursor = md.list_markets(status="open", limit=200, cursor=cursor)
            for market in markets:
                total_scanned += 1
                result = compliance.check_market(market, md)
                if not result.allowed:
                    continue
                vol = volume_fp(market)
                if vol < _SCAN_MIN_VOLUME:
                    continue
                expiry = market.expected_expiration_time or market.expiration_time
                hours = hours_until(expiry)
                if hours is None or not (_SCAN_MIN_HOURS <= hours <= _SCAN_MAX_HOURS):
                    continue
                best = None
                for side, bid, ask in [
                    ("yes", market.yes_bid, market.yes_ask),
                    ("no", market.no_bid, market.no_ask),
                ]:
                    if bid is None or not (_SCAN_MIN_PRICE <= bid <= _SCAN_MAX_PRICE):
                        continue
                    if ask is None:
                        continue
                    spread = ask - bid
                    if spread >= _SCAN_MAX_SPREAD:
                        continue
                    if best is None or bid < best["price_d"]:
                        best = {
                            "ticker": market.ticker,
                            "title": market.title or market.ticker,
                            "category": result.category,
                            "side": side,
                            "price": str(bid),
                            "spread": str(spread),
                            "score": round(float(bid) * hours, 3),
                            "hours_to_expiry": round(hours, 1),
                            "volume_fp": vol,
                            "price_d": bid,
                        }
                if best:
                    candidates.append(best)
            if not cursor:
                break

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return {
            "scanned_at": int(time.time() * 1000),
            "total_scanned": total_scanned,
            # Store all candidates; slice at return time so different limit values
            # can reuse the same cached scan.
            "candidates": [{k: v for k, v in c.items() if k != "price_d"} for c in candidates],
        }

    try:
        full = cached("signals", _SIGNALS_TTL, _scan)
        return {**full, "candidates": full["candidates"][:limit]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
