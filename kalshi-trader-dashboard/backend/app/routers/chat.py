"""Agent chat router — Claude API-backed conversations with executor or research context."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kalshi_agent_trader.portfolio import Portfolio

from ..deps import get_client, get_config
from ..kalshi_tools import EXECUTOR_TOOLS, RESEARCH_TOOLS, execute_tool

router = APIRouter(prefix="/chat", tags=["chat"])

_EXECUTOR_SYSTEM = """You are the Executor Agent for a Kalshi prediction market trading system.
Operator: {username}

## Your tools
- `get_account_status` — balance + open positions + resting (unfilled) orders. Use this first for any portfolio question.
- `get_positions` — open market positions only (does NOT include resting orders).
- `list_markets` / `get_events` — live market data (public, no auth required).
- `get_orderbook` — live bid/ask depth for a ticker.
- `run_three_leg_screen` — Kelly-sized three-leg fatigue-hedge candidates.
- `submit_order` — routes through compliance → risk → execution. Dry-run by default; says so in the result.
- `cancel_order` — cancel a resting order by ID.

## Rules
- Always call a tool to get current data before making claims about positions, prices, or order status.
- Never guess at prices, fills, or exposure — fetch them.
- When recommending an order, state ticker, side, price, count, and rationale.
- All orders go through compliance → risk gates — a rejection is informative, not an error.

## Current system state
{context}"""

_RESEARCH_SYSTEM = """You are the Research Agent for a Kalshi prediction market trading system.
Operator: {username}

## Your tools
- `get_account_status` — balance + open positions + resting (unfilled) orders. Use this first for any portfolio question. `get_positions` alone does NOT return resting orders.
- `get_positions` — open market positions only.
- `list_markets` / `get_events` — live market data.
- `get_orderbook` — live bid/ask depth for a ticker.
- `run_three_leg_screen` — Kelly-sized three-leg fatigue-hedge candidates for tennis (e.g. French Open).

## Rules
- Always call a tool before making claims about prices, positions, or order status. Do not rely solely on the context snapshot below — it may be stale.
- Be quantitative: cite de-vigged fairs, EV, Kelly sizes.
- You do not place orders. Hand off a GO/NO-GO recommendation to the Executor.

## Current system state (snapshot — may lag by up to 30s)
{context}"""

_MAX_TOOL_ITERATIONS = 5


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
    images: list[dict] = []  # [{media_type: str, data: str}]


@router.post("")
def chat(req: ChatRequest):
    cfg = get_config()
    api_key = cfg.secrets.anthropic_api_key
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
    tools = EXECUTOR_TOOLS if req.agent == "executor" else RESEARCH_TOOLS

    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in req.history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if req.images:
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]}}
            for img in req.images
        ] + [{"type": "text", "text": req.message}]
    else:
        user_content = req.message
    messages.append({"role": "user", "content": user_content})

    try:
        ac = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com")

        for _ in range(_MAX_TOOL_ITERATIONS):
            resp = ac.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            if resp.stop_reason != "tool_use":
                break

            # Collect all tool calls and execute them.
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input, client, cfg)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # Append assistant turn + tool results and loop.
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": tool_results})

        # Extract the final text reply.
        reply = next(
            (block.text for block in resp.content if hasattr(block, "text")),
            "(no text response)",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")

    return {"reply": reply}
