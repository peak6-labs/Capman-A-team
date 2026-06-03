"""Agent chat router — Claude API-backed conversations with executor or research context."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client, get_config

router = APIRouter(prefix="/chat", tags=["chat"])

_EXECUTOR_SYSTEM = """You are the Executor Agent for a Kalshi prediction market trading system.
Your role is to help the operator ({username}) make execution decisions — order placement, timing,
compliance checks, risk gate assessment, and position management.

You have access to current portfolio context below. Be concise and direct. When you recommend
an action, state the rationale and any compliance or risk considerations. Never place orders
yourself — your job is to advise and reason through execution decisions with {username}.

{context}"""

_RESEARCH_SYSTEM = """You are the Research Agent for a Kalshi prediction market trading system.
Your role is to help the operator ({username}) identify trading opportunities, assess market edges,
screen for three-leg fatigue-hedge trades (e.g. French Open), and evaluate signal quality.

You have access to current portfolio context below. Focus on edge identification, probability
assessment, and trade structuring. Be quantitative where possible. Never place orders yourself.

{context}"""


def _build_context(client) -> str:
    try:
        cfg = get_config()
        kill = getattr(cfg, "kill_switch_engaged", False)
        dry_run = getattr(cfg, "dry_run", True)
        positions = Portfolio(client).market_positions()
    except Exception:
        return "Portfolio context unavailable."

    lines = [
        f"Kill switch: {'ENGAGED' if kill else 'clear'}",
        f"Mode: {'dry run (no live orders)' if dry_run else 'LIVE'}",
        f"Open positions: {len(positions)}",
    ]
    for p in positions[:10]:
        ticker = p.get("ticker", "?")
        pos = p.get("position_fp")
        exposure = p.get("market_exposure_dollars") or p.get("position_cost_dollars")
        lines.append(
            f"  • {ticker}  pos={pos}  exposure=${float(exposure or 0):.2f}"
        )
    if len(positions) > 10:
        lines.append(f"  … and {len(positions) - 10} more")
    return "\n".join(lines)


class ChatRequest(BaseModel):
    message: str
    agent: Literal["executor", "research"] = "research"
    history: list[dict] = []
    username: str = "operator"


@router.post("")
def chat(req: ChatRequest):
    api_key = get_config().secrets.anthropic_api_key
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set. Add it to kalshi-agent-trader/.env to enable agent chat.",
        )

    try:
        import anthropic
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="anthropic package not installed. Run: pip install anthropic",
        )

    client = get_client()
    context = _build_context(client)
    template = _EXECUTOR_SYSTEM if req.agent == "executor" else _RESEARCH_SYSTEM
    system_prompt = template.format(context=context, username=req.username)

    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in req.history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    messages.append({"role": "user", "content": req.message})

    try:
        # Explicitly set base_url to override any empty ANTHROPIC_BASE_URL env var.
        ac = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com")
        resp = ac.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        reply = resp.content[0].text
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")

    return {"reply": reply}
