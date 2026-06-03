"""Control router: kill-switch + dry_run toggles + exchange/auth status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kalshi_agent_trader.client import KalshiError
from kalshi_agent_trader.risk import RiskGate

from ..config_writer import (
    clear_kill_switch,
    engage_kill_switch,
    kill_switch_engaged,
    set_dry_run,
)
from ..deps import config_write_lock, get_client, get_config, reset_client

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
