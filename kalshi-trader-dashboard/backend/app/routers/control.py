"""Control router: kill-switch + dry_run toggles + exchange/auth status + live signal scan."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from kalshi_agent_trader.client import KalshiError
from kalshi_agent_trader.compliance import ComplianceGate
from kalshi_agent_trader.market_data import MarketData

from ..analyzers import SignalCandidate, find_short_expiry_liquid, find_tail_opportunities
from ..config_writer import (
    clear_kill_switch,
    engage_kill_switch,
    kill_switch_engaged,
    set_dry_run,
)
from ..deps import get_client, get_config, reset_client

_SCAN_PAGE_LIMIT = 1000
_TAIL_THRESHOLD = 0.08
_TAIL_MAX_SPREAD = 0.05
_TAIL_MIN_VOL_24H = 100.0
_TAIL_MIN_HOURS = 2.0
_SEL_MAX_HOURS = 72.0
_SEL_MIN_HOURS = 1.0
_SEL_MIN_VOL_24H = 200.0
_COMPLIANCE_CANDIDATE_LIMIT = 250
_MIN_COMPLIANCE_LOOKUPS = 20
_SIGNALS_TTL = 60  # seconds
_SIGNALS_DEFAULT_LIMIT = 10
_SIGNALS_ERROR_COOLDOWN_S = 60
_SIGNALS_RATE_LIMIT_COOLDOWN_S = 180

router = APIRouter(prefix="/control", tags=["control"])

_signals_lock = threading.Lock()
_signals_snapshot: dict[str, Any] | None = None
_signals_running = False
_signals_error: str | None = None
_signals_started_at: int | None = None
_signals_next_retry_at: int | None = None
_signals_capacity = 0


class KillRequest(BaseModel):
    engaged: bool


class DryRunRequest(BaseModel):
    dry_run: bool


def _category_for_event(
    md: MarketData,
    event_ticker: str,
    category_cache: dict[str, str | None],
) -> str | None:
    if event_ticker not in category_cache:
        category_cache[event_ticker] = md.get_event(event_ticker).category
    return category_cache[event_ticker]


def _candidate_payload(candidate: SignalCandidate, category: str | None) -> dict[str, Any]:
    return {
        "ticker": candidate.ticker,
        "title": candidate.title,
        "category": category,
        "side": candidate.side,
        "price": str(candidate.price),
        "spread": str(candidate.spread),
        "score": candidate.score,
        "hours_to_expiry": candidate.hours_left,
        "volume_fp": float(candidate.volume_24h),
    }


def scan_signal_candidates(cfg, client, *, result_limit: int = 10) -> dict[str, Any]:
    md = MarketData(client)
    compliance = ComplianceGate(cfg.compliance)
    all_markets = []
    candidates = []
    total_scanned = 0
    cursor = None

    while True:
        markets, cursor = md.list_markets(status="open", limit=_SCAN_PAGE_LIMIT, cursor=cursor)
        all_markets.extend(markets)
        total_scanned += len(markets)
        if not cursor or not markets:
            break

    market_index = {market.ticker: market for market in all_markets}
    tail_results = find_tail_opportunities(
        all_markets,
        tail_threshold=_TAIL_THRESHOLD,
        max_spread=_TAIL_MAX_SPREAD,
        min_volume_24h=_TAIL_MIN_VOL_24H,
        min_hours=_TAIL_MIN_HOURS,
    )
    short_expiry_results = find_short_expiry_liquid(
        all_markets,
        max_hours=_SEL_MAX_HOURS,
        min_hours=_SEL_MIN_HOURS,
        min_volume_24h=_SEL_MIN_VOL_24H,
    )

    merged = []
    seen = set()
    for raw in [*tail_results, *short_expiry_results]:
        if raw.ticker in seen:
            continue
        seen.add(raw.ticker)
        merged.append(raw)

    merged.sort(key=lambda raw: raw.score, reverse=True)
    compliance_lookup_limit = min(
        _COMPLIANCE_CANDIDATE_LIMIT,
        max(_MIN_COMPLIANCE_LOOKUPS, result_limit * 6),
    )
    merged = merged[:compliance_lookup_limit]
    event_categories: dict[str, str | None] = {}
    for raw in merged:
        market = market_index.get(raw.ticker)
        if market is None:
            continue
        category = _category_for_event(md, market.event_ticker, event_categories)
        result = compliance.evaluate(
            category=category,
            title=market.title or raw.title or "",
        )
        if not result.allowed:
            continue
        candidates.append(_candidate_payload(raw, result.category))
        if len(candidates) >= result_limit:
            break

    return {
        "scanned_at": int(time.time() * 1000),
        "total_scanned": total_scanned,
        "candidates": candidates,
    }


def _scan_is_fresh(snapshot: dict[str, Any] | None) -> bool:
    if not snapshot:
        return False
    scanned_at = snapshot.get("scanned_at")
    return isinstance(scanned_at, int) and (time.time() * 1000 - scanned_at) < _SIGNALS_TTL * 1000


def _run_signals_scan(cfg, result_limit: int) -> None:
    global _signals_snapshot, _signals_running, _signals_error, _signals_next_retry_at, _signals_capacity

    try:
        snapshot = scan_signal_candidates(cfg, get_client(), result_limit=result_limit)
    except Exception as exc:
        cooldown_s = (
            _SIGNALS_RATE_LIMIT_COOLDOWN_S
            if isinstance(exc, KalshiError) and exc.status == 429
            else _SIGNALS_ERROR_COOLDOWN_S
        )
        with _signals_lock:
            _signals_error = str(exc)
            _signals_next_retry_at = int((time.time() + cooldown_s) * 1000)
    else:
        with _signals_lock:
            _signals_snapshot = snapshot
            _signals_error = None
            _signals_next_retry_at = None
            _signals_capacity = result_limit
    finally:
        with _signals_lock:
            _signals_running = False


def _ensure_signals_scan(cfg, result_limit: int) -> None:
    global _signals_running, _signals_started_at

    with _signals_lock:
        now_ms = int(time.time() * 1000)
        if _signals_running:
            return
        if _signals_capacity >= result_limit and _scan_is_fresh(_signals_snapshot):
            return
        if _signals_next_retry_at and now_ms < _signals_next_retry_at:
            return
        _signals_running = True
        _signals_started_at = now_ms

    thread = threading.Thread(
        target=_run_signals_scan,
        args=(cfg, result_limit),
        daemon=True,
        name="signal-scan",
    )
    thread.start()


def _signals_response(limit: int) -> dict[str, Any]:
    with _signals_lock:
        snapshot = dict(_signals_snapshot) if _signals_snapshot else {
            "scanned_at": 0,
            "total_scanned": 0,
            "candidates": [],
        }
        running = _signals_running
        error = _signals_error
        started_at = _signals_started_at
        next_retry_at = _signals_next_retry_at

    snapshot["candidates"] = snapshot.get("candidates", [])[:limit]
    snapshot["scan_status"] = "running" if running else "error" if error else "ready"
    snapshot["scan_error"] = error
    snapshot["scan_started_at"] = started_at
    snapshot["scan_next_retry_at"] = next_retry_at
    return snapshot


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
        actual = set_dry_run(req.dry_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # Reset client so fresh config is picked up next call.
    reset_client()
    return {"dry_run": actual}


@router.get("/signals")
def live_signals(
    limit: int = Query(default=10, ge=1, le=50),
    refresh: bool = Query(default=False),
):
    """Start or observe the all-market signal scan without blocking the UI."""
    if refresh:
        cfg = get_config()
        _ensure_signals_scan(cfg, max(limit, _SIGNALS_DEFAULT_LIMIT))
    return _signals_response(limit)
